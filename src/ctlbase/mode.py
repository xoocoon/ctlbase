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

"""Low-level classes for mode handling.

For the core concepts of mode handling see the
:ref:`control module <control-mode>`.

Controls should use the high-level `Mode` methods of :class:`~.Control`.
"""

import sys
import os
import grp
import glob

import time
from enum import Enum
import re

from ctlbase.config import Caller
from ctlbase.message import Severity, ErrorMessage, MessageHelper


TIMEOUT_DEFAULT_S = 30
"""Default timeout for a pending mode in seconds."""

BASE_DIR_PATH_DEFAULT = '/tmp'
"""Default base directory under which mode directories are created."""

FILE_PREFIX_PENDING = 'mode-pending_'
FILE_PREFIX_ACTIVE = 'mode-active'
FILE_SUFFIX = '.txt'


class ModeDirHelper:
    """Static functions for dealing with mode directories, i.e. directories 
    where modes are tracked.
    """

    @staticmethod
    def isDirWritable(dirPath):
        if os.path.isdir(dirPath):
            return os.access(dirPath, os.W_OK)

        # Check if a sub directory can be created.
        return os.access(os.path.dirname(dirPath), os.W_OK)

    @staticmethod
    def find(dirName, isUseCallerName=True, isUseScriptName=True):
        """Tries to find a mode directory with the given parameters.
                
        An eligible mode directory must be writable to the current user. 
        
        To avoid the runtime overhead of auto-discovery, provide an absolute
        path in :paramref:`~.ModeDirHelper.find.dirName`.
        
        :param dirName: The mode directory name. It may include a relative or
            absolute path specification. `True` for auto-discovery.
        :type dirName: str | True
        :param isUseCallerName: If `True`, the caller's normalized module name
            is used as a directory name, as returned by
            :meth:`~.CallerInspector.getName`. Only relevant if 
            :paramref:`~.ModeDirHelper.find.dirName` is `True`.
        :type isUseCallerName: bool
        :param isUseScriptName: If `True`, the current script's normalized
            module name is used as a directory name, based on ``sys.argv[0]``. 
            Only relevant if :paramref:`~.ModeDirHelper.find.dirName` is `True`.
            :paramref:`~.FileHelper.find.isUseCallerName` takes precedence.
        :type isUseScriptName: bool
        
        :return: The absolute real path of the directory if found.
        :rtype: str
        
        :raises FileNotFoundError: If the directory could not be found or is not
            writable for the current user.
        """

        if not dirName:
            raise ValueError("dirName must be given.")

        if isinstance(dirName, str):
            if '/' not in dirName:
                # If the given path does not contain slashes, treat it as base name.
                dirName = '%s/%s' % (BASE_DIR_PATH_DEFAULT, dirName)
            if ModeDirHelper.isDirWritable(dirName):
                return os.path.realpath(dirName)
            raise ValueError("%s is not writable for the current user." % dirName)
        elif not isUseCallerName and not isUseScriptName:
            raise ValueError("At least one of (isUseCallerName | isUseScriptName) must be True.")

        if isUseCallerName:
            dirName = Caller.getName()
            dirName = '%s/%s' % (BASE_DIR_PATH_DEFAULT, dirName)
            if ModeDirHelper.isDirWritable(dirName):
                return dirName

        if isUseScriptName and len(sys.argv):
            dirName = Caller.normalizeName(sys.argv[0])
            dirName = '%s/%s' % (BASE_DIR_PATH_DEFAULT, dirName)
            if ModeDirHelper.isDirWritable(dirName):
                return dirName

        raise FileNotFoundError("Could not find mode directory%s." % (' %s' % dirName if dirName is not None else ''))


class FileModeHandler():
    """A mode handler using text files for persistence."""

    @staticmethod
    def getFutureTimestamp(seconds: int):
        now = time.time_ns() // 1000000000
        return now + seconds

    @staticmethod
    def hasExpired(timestamp: int):
        now = time.time_ns() // 1000000000
        return bool(now > timestamp)

    def __init__(self, dirPath=None):
        """Creates a new mode handler operating in a specific directory.
        
        :param dirPath: The path to the mode directory. It may be specified as
            described under :paramref:`~.ModeDirHelper.find.dirName`.Normally, 
            a directory in a tmpfs is sufficient. If modes should survive system
            reboots, choose a directory on a persistent file system.
        :type dirPath: str
        """

        self.dirPath = ModeDirHelper.find(dirPath)
        self.filePrefixPending = FILE_PREFIX_PENDING
        self.filePrefixActive = FILE_PREFIX_ACTIVE
        self.fileSuffix = FILE_SUFFIX

        self.pending = None
        self.active = None

    def removeDirectory(self):
        try:
            os.rmdir(self.dirPath)
        except:
            # If the directory is not empty, simply ignore the error.
            pass

    def getPendingFilePaths(self):
        return glob.glob('%s/%s%s%s' % (self.dirPath, self.filePrefixPending, '*', self.fileSuffix))

    def getPending(self):
        """Retrieves the pending mode by identifying and reading the pending 
        mode file with the most current modification time.
        
        Also removes the files of expired pending modes.
        
        :return: The pending mode if set, otherwise ``none`` (`str`).
        :rtype: str
        """

        self.cleanupExpiredFiles()

        for filePath in sorted(self.getPendingFilePaths(), key=os.path.getmtime, reverse=True):
            with open(filePath) as file:
                mode = file.read()
            self.pending = mode
            return mode

        if self.pending:
            return self.pending

        return 'none'

    def unsetPending(self):
        """Unsets the pending mode by deleting all existing pending mode files.
        
        Removes the mode directory altogether if no files are left.
        """

        try:
            for filePath in self.getPendingFilePaths():
                os.remove(filePath)
        except Exception as error:
            if self.pending:
                raise error
        finally:
            self.pending = None
            self.removeDirectory()

    def setPending(self, mode, timeout_s=-1, group=None):
        """Sets the pending mode by creating a new pending mode file.
        
        If :paramref:`~.FileModeHandler.setActive.group` is not `None`, the
        group ownership is only set if the current user is the owner of the mode
        directory and file, respectively. Otherwise, the ownership is left 
        unmodified.
        
        :param mode: The mode to set. Passing an empty `str` or ``none`` is 
            equivalent to calling :meth:`~.Control.unsetPending`. 
        :type mode: str
        :param timeout_s: The mode timeout in seconds. If ``-1``, the value of
            :data:`~.TIMEOUT_DEFAULT_S` is assumed. Passing ``0`` is equivalent
            to calling :meth:`~.Control.unsetPending`. 
        :type timeout_s: int | None
        :param group: The group ownership to be set on the mode directory and 
            file. Accepts a group name or numerical group ID (gid).
        :type group: str | None
        
        :raise ValueError: If the given group does not exist.
        
        :return: `True` if the mode was set, `False` if it was unset.
        :rtype: bool
        """

        if timeout_s in (-1, None):
            timeout_s = TIMEOUT_DEFAULT_S

        if mode in ('', 'none') or timeout_s == 0:
            self.unsetPending()
            return False

        if isinstance(mode, Enum):
            mode = mode.value

        gid = None
        if group:
            if isinstance(group, int) or group.isdigit():
                gid = int(group)
            else:
                try:
                    gid = grp.getgrnam(group).gr_gid
                except KeyError:
                    raise ValueError("Group %s does not exist." % group)

        filePath = '%s/%s%i%s' % (self.dirPath, self.filePrefixPending, FileModeHandler.getFutureTimestamp(timeout_s), self.fileSuffix)

        if not os.path.exists(self.dirPath):
            os.makedirs(self.dirPath)

        with open(filePath, 'w') as file:
            file.write(mode)

        self.pending = mode

        if not gid:
            return True

        # If the uid of the current process is the owner of the directory, 
        # update its group permissions.
        if os.stat(self.dirPath).st_uid == os.getuid():
            os.chown(self.dirPath, -1, gid)
            os.chmod(self.dirPath, 0o771)

        os.chown(filePath, -1, gid)
        os.chmod(filePath, 0o660)
        
        return True

    def getActiveFilePath(self):
        return '%s/%s%s' % (self.dirPath, self.filePrefixActive, self.fileSuffix)

    def getActive(self):
        """Retrieves the active mode by reading the active mode file.
        
        :return: The active mode if set, otherwise ``none`` (`str`).
        :rtype: str
        """

        activeFilePath = self.getActiveFilePath()

        if os.path.exists(activeFilePath):
            with open(activeFilePath) as file:
                mode = file.read()
            self.active = mode
            return mode

        if self.active:
            return self.active

        return 'none'

    def unsetActive(self):
        """Unsets the active mode by deleting the active mode file if it exists.
        
        Removes the mode directory if no files are left.
        """

        try:
            os.remove(self.getActiveFilePath())
        except Exception as error:
            if self.active:
                raise error
        finally:
            self.active = None
            self.cleanupExpiredFiles()

    def setActive(self, mode, group=None):
        """Sets the active mode by creating an active mode file or overwriting
        an existing one.
        
        If :paramref:`~.FileModeHandler.setActive.group` is not `None`, the
        group ownership is only set if the current user is the owner of the mode
        directory and file, respectively. Otherwise, the ownership is left 
        unmodified.
        
        :param mode: The mode to set. Passing an empty `str` or ``none`` is 
            equivalent to calling :meth:`~.Control.unsetActive`. 
        :type mode: str
        :param group: The group ownership to be set on the mode directory and 
            file. Accepts a group name or numerical group ID (gid).
        :type group: str | None
        
        :raise ValueError: If the given group does not exist.
        
        :return: `True` if the mode was set, `False` if it was unset.
        :rtype: bool
        """

        if mode in ('', 'none'):
            self.unsetActive()
            return False

        if isinstance(mode, Enum):
            mode = mode.value

        gid = None
        if group:
            if isinstance(group, int) or group.isdigit():
                gid = int(group)
            else:
                try:
                    gid = grp.getgrnam(group).gr_gid
                except KeyError:
                    raise ValueError("Group %s does not exist." % group)

        filePath = '%s/%s%s' % (self.dirPath, self.filePrefixActive, self.fileSuffix)

        if not os.path.exists(self.dirPath):
            os.makedirs(self.dirPath)

        with open(filePath, 'w') as file:
            file.write(mode)

        self.active = mode

        if not gid:
            return True

        # If the uid of the current process is the owner of the directory, 
        # update its group permissions.
        if os.stat(self.dirPath).st_uid == os.getuid():
            os.chown(self.dirPath, -1, gid)
            os.chmod(self.dirPath, 0o771)

        if os.stat(filePath).st_uid == os.getuid():
            os.chown(filePath, -1, gid)
            os.chmod(filePath, 0o660)
        
        return True

    def activatePending(self):
        """Activates the pending mode by renaming the pending mode file with the
        most current modification time.
        
        Also removes the files of expired pending modes.
        
        :raise RuntimeError: If there is no pending mode that could be activated.
        
        :return: The activate mode.
        :rtype: str
        """

        self.cleanupExpiredFiles()

        for filePath in sorted(self.getPendingFilePaths(), key=os.path.getmtime, reverse=True):
            with open(filePath) as file:
                mode = file.read()
            os.rename(filePath, self.getActiveFilePath())
            self.active = mode
            return mode

        raise RuntimeError("Could not find any pending mode files to activate.")

    def cleanupExpiredFiles(self):
        for filePath in self.getPendingFilePaths():
            timestamp = int(os.path.basename(filePath).removeprefix(FILE_PREFIX_PENDING).removesuffix(FILE_SUFFIX))

            if FileModeHandler.hasExpired(timestamp):
                os.remove(filePath)

        self.removeDirectory()
