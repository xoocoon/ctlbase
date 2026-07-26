# SPDX-FileCopyrightText: 2026 54350963+xoocoon@users.noreply.github.com
#
# SPDX-License-Identifier: GPL-3.0-only

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

"""Classes for discovering and parsing configuration and other files."""

import sys
import os

import asyncio
import importlib

import re
from io import StringIO
from enum import Enum


TRUE_VALUE_PATTERN = re.compile(r'^(?:1|yes|true)$', flags=re.IGNORECASE)
"""Regular expression for `str` values to be treated as `True`."""

FALSE_VALUE_PATTERN = re.compile(r'^(?:0|no(?:ne)?|false)$', flags=re.IGNORECASE)
"""Regular expression for `str` values to be treated as `False`."""


class CallerInspector:
    """An inspector for the so-called caller,  i.e. the Python module topmost in
    the call stack.
    
    The caller is determined by iterating the call stack, checking the 
    corresponding module file names against a blacklist. By default the list is
    made up of all the module names in the `ctlbase` package.
    
    Once the caller is determined, its normalized name is obtained by applying a
    regex pattern to its module file name, without the path specification.
    
    As an example using the default pattern, a Python module
    ``/usr/local/bin/screenctl.py`` results in the normalized name ``screen``. 
    It can be used as a component in related file names like ``etc/screen``.
    This in turn facilitates the auto-discovery of related files.
    """

    def __init__(self,
        suffixPatternStr=r'(?:ctl|)(?:\.py|\.pyc|\.pyo|)$',
        blacklistPatternStr=None
    ):
        """Creates a caller inspector with a specific configuration.

        :param suffixPatternStr: A regex pattern applied to the end of a Python
            module's file name. If it matches, it is removed from the file name
            to form the caller's normalized module name.
        :type suffixPatternStr: str
        :param blacklistPatternStr: A regex pattern applied to a Python module's
            file name. If the pattern matches, the corresponding module
            skipped in the call hierarchy, i.e. not considered a caller. If
            `None`, a pattern matching the module names in the `ctlbase` package
            is used.
        :type blacklistPatternStr: str | None
        """

        self.packageMembersCached = set()
        ctlbase = importlib.import_module('ctlbase')
        for moduleName in ctlbase.__all__:
            if not moduleName.startswith('_'):
                self.packageMembersCached.add(moduleName)

        self.suffixPatternStr = suffixPatternStr.removesuffix('$') + r'$'

        if blacklistPatternStr is None:
            blacklistPatternStr = r'(?:' + r'|'.join(self.packageMembersCached) + r')' + self.suffixPatternStr
        self.excludeBasenamePattern = re.compile(blacklistPatternStr)

        self.callersCached = {}

    def getPath(self):
        """Determines the caller and obtains its file path.
        
        :return: The caller's real path including the absolute path and file
            name.
        :rtype: str
        """

        stackDepth = 1
        callerPath = os.path.realpath(__file__)

        try:
            while self.excludeBasenamePattern.match(os.path.basename(callerPath)):
                callerPath = os.path.realpath(sys._getframe(stackDepth).f_code.co_filename)
                stackDepth += 1
        except:
            pass

        return callerPath

    def getName(self):
        """Determines the caller and obtains its normalized name.
        
        :return: The caller's normalized name.
        :rtype: str
        """

        callerPath = self.getPath()
        return self._normalizeName(callerPath)
    
    def _normalizeName(self, path):
        if path is None:
            return None

        return re.sub(self.suffixPatternStr, '', os.path.basename(path))


class FileHelper:
    """Static functions for dealing with files."""

    class Type(Enum):
        """The type of a file."""

        CONFIG = 'config'
        """A configuration file."""

        BIN = 'binary'
        """An executable file."""

        TRANSLATION = 'language'
        """A translation file for a specific language."""

    CONFIG_LOCATIONS = (
        'etc/default',
        'etc',
        '/etc/default',
        '/etc'
    )
    """Well-known directory paths for configuration files. May contain both
    relative and absolute paths.
    """

    BIN_LOCATIONS = (
        '.',
        'bin',
        '/usr/bin',
        '/usr/sbin',
        '/usr/local/bin'
    )
    """Well-known directory paths for executable files. May contain both
    relative and absolute paths.
    """
    
    TRANSLATION_LOCATIONS = (
        'locales',
    )
    """Well-known directory paths for translation files. May contain both
    relative and absolute paths.
    """

    @staticmethod
    def isFileReadable(filePath):
        if os.path.exists(filePath):
            return os.access(filePath, os.R_OK)
        return False

    @staticmethod
    def isFileExecutable(filePath):
        if os.path.exists(filePath):
            return os.access(filePath, os.X_OK)
        return False

    @staticmethod
    def _tryLocations(filePath, startDir, checkCallable, locations):
        # Try relative locations first.
        for location in list(locations):
            if location[0] == '/':
                continue

            # Iteratively step up start directory hierarchy.
            parentDir = startDir
            while parentDir not in ('/', ''):
                absolutePath = '%s/%s/%s' % (parentDir, location, filePath)
                if checkCallable(absolutePath):
                    return absolutePath
                parentDir = os.path.dirname(parentDir)

        # Try absolute locations.
        for location in list(locations):
            if location[0] != '/':
                continue

            absolutePath = '%s/%s' % (location, filePath)
            if checkCallable(absolutePath):
                return absolutePath

        return None

    @staticmethod
    def find(fileName, fileType, isStartFromCallerPath=True, isStartFromScriptPath=True):
        """Tries to find a file with the given parameters in a set of well-known
        directories.
        
        The available sets of directories are defined by 
        :attr:`~.CONFIG_LOCATIONS`, :attr:`~.BIN_LOCATIONS` and 
        :attr:`~.TRANSLATION_LOCATIONS`. The actual set is determined by the 
        :paramref:`~.FileHelper.find.fileType` parameter.
        
        Relative path specifications in those sets are applied up the directory
        tree until the file is found or the root of the file system is reached. 
        For instance, if the search for a configuration file named ``file_1.conf`` 
        starts at the directory ``/home/myuser/myapp/``, the existence of 
        ``/home/myuser/myapp/etc/default/file_1.conf`` is checked first, then
        ``/home/myuser/etc/default/file_1.conf``, then 
        ``/home/etc/default/file_1.conf`` and so on.
        
        An eligible target file must be accessible to the current user. That is, 
        it must be readable, or, in the case of an executable file, be executable
        for the current user.
        
        To avoid the runtime overhead of auto-discovery, provide an absolute
        path in :paramref:`~.FileHelper.find.fileName`.
        
        :param fileName: The file name of the searched file. It may include a 
            relative or absolute path specification. `True` for auto-discovery.
        :type fileName: str | True
        :param fileType: The type of the file to search for. It determines the
            set of well-known directories to use. 
        :type fileType: :class:`~.FileHelper.Type`
        :param isStartFromCallerPath: If `True`, relative path specifications 
            are treated relative to the caller's file path as returned by 
            :meth:`~.Caller.getPath`. What is more, if 
            :paramref:`~.FileHelper.find.fileName` is `True`, the caller's
            normalized module name is used as a file name.
        :type isStartFromCallerPath: bool
        :param isStartFromScriptPath: If `True`, relative path specifications
            are treated relative to the current script's path, based on
            ``sys.argv[0]``. What is more, if
            :paramref:`~.FileHelper.find.fileName` is `True`, the script's
            normalized module name is used as a file name.
            :paramref:`~.FileHelper.find.isStartFromCallerPath` takes 
            precedence.
        :type isStartFromScriptPath: bool
        
        :return: The absolute real path of the file if found.
        :rtype: str
        
        :raises FileNotFoundError: If the file could not be found or is 
            inaccessible to the current user.
        """

        if not fileName:
            raise ValueError("fileName must be given.")

        if fileType == FileHelper.Type.CONFIG:
            checkCallable = FileHelper.isFileReadable
            locations = FileHelper.CONFIG_LOCATIONS
        elif fileType == FileHelper.Type.BIN:
            checkCallable = FileHelper.isFileExecutable
            locations = FileHelper.BIN_LOCATIONS
        elif fileType == FileHelper.Type.TRANSLATION:
            checkCallable = FileHelper.isFileReadable
            locations = FileHelper.TRANSLATION_LOCATIONS

        if isinstance(fileName, str) and fileName.startswith('/'):
            if checkCallable(fileName):
                return os.path.realpath(fileName)
            raise ValueError("%s is not %s for the current user." % (fileName, 'executable' if fileType == FileHelper.Type.BIN else 'readable'))
        elif not isStartFromCallerPath and not isStartFromScriptPath:
            raise ValueError("At least one of (isStartFromCallerPath | isStartFromScriptPath) must be True.")

        if isStartFromCallerPath:
            startDir = Caller.getPath()
            if isinstance(fileName, bool) and fileName:
                fileName = Caller._normalizeName(startDir)
            startDir = os.path.dirname(startDir)

            absolutePath = FileHelper._tryLocations(fileName, startDir, checkCallable, locations)
            if absolutePath:
                return os.path.realpath(absolutePath)

        if isStartFromScriptPath and len(sys.argv):
            if isinstance(fileName, bool) and fileName:
                fileName = Caller._normalizeName(sys.argv[0])
            startDir = os.path.dirname(os.path.realpath(sys.argv[0]))

            absolutePath = FileHelper._tryLocations(fileName, startDir, checkCallable, locations)
            if absolutePath:
                return os.path.realpath(absolutePath)

        raise FileNotFoundError("Could not find %s file%s." % (fileType.value, ' %s' % fileName if fileName else ''))

    @staticmethod
    def findConfig(fileName, isStartFromCallerPath=True, isStartFromScriptPath=True):
        """Convenience method for finding configuration files.
        
        Corresponds to the :attr:`~.FileHelper.Type.CONFIG` type.
        
        The parameters have the same meanings as with :meth:`~.FileHelper.find`.
        """

        return FileHelper.find(fileName, FileHelper.Type.CONFIG, isStartFromCallerPath, isStartFromScriptPath)

    @staticmethod
    def findBinary(fileName, isStartFromCallerPath=True, isStartFromScriptPath=True):
        """Convenience method for finding configuration files.
        
        Corresponds to the :attr:`~.FileHelper.Type.BIN` type.
        
        The parameters have the same meanings as with :meth:`~.FileHelper.find`.
        """

        return FileHelper.find(fileName, FileHelper.Type.BIN, isStartFromCallerPath, isStartFromScriptPath)
    
    @staticmethod
    def findTranslation(fileName, lng=None, isStartFromCallerPath=True, isStartFromScriptPath=True):
        """Convenience method for finding configuration files.
        
        Corresponds to the :attr:`~.FileHelper.Type.TRANSLATION` type.
        
        :param lng: A two-letter language code for the translation file to find,
             e.g. ``de``. If given, 
             If :paramref:`~.FileHelper.findTranslation.fileName` must only
             be a file name without a path.
        :type lng: str
        
        The other parameters have the same meanings as with :meth:`~.FileHelper.find`.
        """

        if lng is not None:
            if '/' in fileName:
                raise ValueError("If lng is given, fileName must only contain a file name without a path.")
            if not re.search(r'^[a-z]{2}', lng):
                raise ValueError("lng must be a two-letter language code e.g. 'de'.")
            fileName = lng + '/' + fileName
        return FileHelper.find(fileName, FileHelper.Type.TRANSLATION, isStartFromCallerPath, isStartFromScriptPath)


class BashHelper():
    """Static functions for dealing with Bash-style configuration files.
    
    That is, Bash-style variable assignments are supported, including 
    interpolation of the following environment variables:
    
    * ``$$``: The process ID (PID) of the current process.
    * ``$EUID``: The effective user ID of the user executing the process.
    * ``HOME``: The home directory of the user executing the process.
    * ``PPID``: The process ID of the parent process of the current process.
    * ``PWD``: The current working directory.
    * ``UID``: The user ID of the user executing the process.
    
    Within the configuration file, former variables are interpolated in later
    variables. As an example, if ``FILE_INDEX=1`` is followed by 
    ``FILE_NAME=file_$FILE_INDEX.conf``, then ``FILE_NAME`` is resolved to
    ``file_1.conf``. Curly brace syntax is supported, i.e. ``$FILE_INDEX`` is
    equivalent to ``${FILE_INDEX}``.
    
    Regarding data types, values are parsed as follows:
    
    * Integer literals declared with ``declare -i`` are cast to ``int``.
    * String literals enclosed in ``'`` or ``"`` are cast to ``str``, including spaces.
    * Array literals starting with ``(`` and ending with ``)`` are cast to ``list`` unless ...
    * ... they contain entries in the form ``[key]=value``. Then they are cast to ``dict``.
    """

    LITERAL_PATTERN_STR = r'(\'.*?\'|".*?"|[^\s]+|[^\n]+)'
    #                                                type                         name  array               literal
    VARIABLE_PATTERN = re.compile(r'^\s*(?:(?:declare\s+(-i|-a|-A)\s+|export\s+))?(\w+)=(?:\(\s*(.*?)\s*\))?(?:' + LITERAL_PATTERN_STR + r')?', flags=re.MULTILINE | re.DOTALL)
    #                                   associative key
    ARRAY_PATTERN = re.compile(r'\s*(?:\[' + LITERAL_PATTERN_STR + r'\]=)?' + LITERAL_PATTERN_STR, flags=re.MULTILINE | re.DOTALL)
    INTERPOLATE_PATTERN = re.compile(r'\$(\(.+?\)|\{.+?\}|[\S]+)')
    
    class Scalar(Enum):
        INTEGER = '-i'

    class Array(Enum):
        UNDEFINED = ''
        INDEXED = '-a'
        ASSOCIATIVE = '-A'

    @staticmethod
    def _getDefaultInterpolationDict():
        interpolationDict = {}
        interpolationDict['$'] = os.getpid()
        interpolationDict['EUID'] = os.geteuid()
        interpolationDict['HOME'] = os.path.expanduser('~')
        interpolationDict['PPID'] = os.getppid()
        interpolationDict['PWD'] = os.getcwd()
        interpolationDict['UID'] = os.getuid()
        return interpolationDict

    @staticmethod
    def _castLiteral(literalStr, literalType=None, interpolationDict=None):
        if literalStr is None or literalStr == '':
            return literalStr

        if isinstance(literalType, str):
            try:
                literalType = BashHelper.Scalar(literalType)
            except KeyError:
                try:
                    literalType = BashHelper.Array(literalType)
                except KeyError:
                    raise ValueError("Type %s is not supported." % literalType)

        if interpolationDict is not None:
            buffer = StringIO()

            match = BashHelper.INTERPOLATE_PATTERN.search(literalStr)
            lastEndIndex = 0
            while match:
                matchStr = match.group(1)
                startIndex = match.start(1)
                endIndex = match.end(1)
                nameStr = None
                expansionStr = None
                if matchStr:
                    if matchStr[0] == '{':
                        # parameter expansion
                        nameStr = matchStr.strip('{}')
                    else:
                        # variable expansion
                        nameStr = matchStr

                    if interpolationDict is not None and nameStr is not None:
                        # variable expansion
                        if nameStr in interpolationDict:
                            expansionStr = interpolationDict[nameStr]
                        else:
                            expansionStr = '${%s:unresolvable}' % nameStr
                    elif expansionStr is None:
                        # fallback: leave literal as it is
                        expansionStr = '$%s' % matchStr

                    #                                    remove dollar sign
                    buffer.write(literalStr[lastEndIndex:(startIndex - 1)])
                    buffer.write(expansionStr)

                    lastEndIndex = endIndex
                    match = BashHelper.INTERPOLATE_PATTERN.search(literalStr, endIndex)

            if lastEndIndex:
                buffer.write(literalStr[lastEndIndex:])

            interpolatedStr = buffer.getvalue()
            if interpolatedStr:
                literalStr = interpolatedStr

        if literalType == BashHelper.Scalar.INTEGER:
            return 0 if not literalStr.isdigit() else int(literalStr)

        if literalType in (BashHelper.Array.INDEXED, BashHelper.Array.ASSOCIATIVE):
            return BashHelper._parseArray(literalStr, literalType, interpolationDict=interpolationDict)

        return literalStr

    @staticmethod
    def _unpackLiteral(literalStr):
        if literalStr is None or literalStr == '':
            return (False, literalStr)

        if literalStr.startswith('\''):
            return (False, literalStr.strip('\''))

        return (True, literalStr.strip('"'))

    @staticmethod
    def _parseArray(arrayStr, arrayType=None, interpolationDict=None):
        if arrayStr is None:
            return None

        if arrayStr == '':
            return []

        if isinstance(arrayType, str):
            try:
                arrayType = BashHelper.Array(arrayType)
            except KeyError:
                raise TypeError("%s is no array type." % arrayType)

        arrayList = []
        arrayDict = {}

        for match in BashHelper.ARRAY_PATTERN.finditer(arrayStr):
            mayInterpolate, keyStr = BashHelper._unpackLiteral(match.group(1))
            mayInterpolate, valueStr = BashHelper._unpackLiteral(match.group(2))

            value = BashHelper._castLiteral(
                valueStr,
                interpolationDict=interpolationDict if mayInterpolate else None
            )

            if keyStr is not None and keyStr != '':
                if arrayType and arrayType == BashHelper.Array.INDEXED:
                    raise TypeError("Converting from associative arrays into index arrays is not possible.")

                if arrayType is None:
                    arrayType = BashHelper.Array.ASSOCIATIVE

                arrayDict[keyStr] = value
            elif (valueStr is not None and valueStr != ''):
                if arrayType and arrayType == BashHelper.Array.ASSOCIATIVE:
                    raise TypeError("A field index is required is required for assignment to an associate array.")

                if arrayType is None:
                    arrayType = BashHelper.Array.INDEXED

                arrayList.append(value)

        if len(arrayList):
            return arrayList

        if len(arrayDict):
            return arrayDict

        return None

    @staticmethod
    def parseConfig(configName, interpolationDict={}):
        """Finds a Bash-like configuration file and parses it into a `dict` if
        found.
        
        :param configName: The configuration file name. It may include a 
            relative or absolute path specification. `True` for auto-discovering
            a configuration file based on the caller's normalized module name as
            returned by :meth:`~.CallerInspector.getName`.
            See :meth:`~.FileHelper.find` for the discovery algorithm.
        :type configName: str | True
        :param interpolationDict: An additionl `dict` used for variable
            interpolation. As an example, if the `dict` contains a key 
            ``MY_INDEX``, its value will substitute all occurrences of 
            ``$MY_INDEX`` and ``${MY_INDEX}`` in the configuration file.
        :type interpolationDict: dict | None
        
        :return: A `dict` containg the Bash-like variables names as keys and 
            their parsed values as values.
        :rtype: dict
        """

        isOwnLoop = False
        isOwnCommandExecutor = False
        loop = None
        try:
            allowDiscovery = False
            if not configName:
                raise ValueError("configName must be given.")
            elif isinstance(configName, bool) and configName:
                allowDiscovery = True
            elif isinstance(configName, str):
                allowDiscovery = True

            configName = FileHelper.findConfig(configName, isStartFromCallerPath=allowDiscovery, isStartFromScriptPath=allowDiscovery)

            with open(configName) as file:
                configStr = file.read()

            if isinstance(interpolationDict, dict):
                interpolationDict.update(BashHelper._getDefaultInterpolationDict())

            configDict = {}
            for match in BashHelper.VARIABLE_PATTERN.finditer(configStr):
                typeStr = match.group(1)
                nameStr = match.group(2)

                arrayStr = match.group(3)
                value = BashHelper._parseArray(
                    arrayStr,
                    arrayType=typeStr,
                    interpolationDict=interpolationDict
                ) if arrayStr else None

                if not value:
                    mayInterpolate, valueStr = BashHelper._unpackLiteral(match.group(4))
                    value = BashHelper._castLiteral(
                        valueStr,
                        literalType=typeStr,
                        interpolationDict=interpolationDict if mayInterpolate else None
                    )

                configDict[nameStr] = value
                if interpolationDict is not None:
                    interpolationDict[nameStr] = value

            return configName, configDict
        finally:
            if isOwnLoop and loop:
                loop.close()


Caller = CallerInspector()
"""Singleton for inspecting the caller."""
