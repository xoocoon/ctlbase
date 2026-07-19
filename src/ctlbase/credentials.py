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

"""Classes for provisioning credentials like user names and passwords to 
:ref:`controls <control-control>`.

The following credential sources are supported:

* `Freedesktop Secret Service <https://specifications.freedesktop.org/secret-service/latest/>`_
  and other keyrings via the python `keyring <https://pypi.org/project/keyring/>`_ 
  package. See :class:`~.KeyringReader`.
* TPM2 via
  `tpm2-tools <https://tpm2-tools.readthedocs.io/en/latest/>`_ binaries. See
  :class:`~.Tpm2Reader`.
* Flat files, either in 
  `netrc <https://everything.curl.dev/usingcurl/netrc.html>`_ format or
  with a single line containing a credential. See
  :class:`~.FileReader`.
* Interactive user prompts. See :class:`~.PromptReader`.

For providing credentials in one of various output formats, the 
:class:`~.TemporaryCredentials` class creates a temporary file and auto-deletes
it again. It is inspired by 
`systemd's credentials mechanism <https://systemd.io/CREDENTIALS/>`_. The
supported output formats are listed in :class:`~.TemporaryCredentials.Format`.

**Note**: Due to inherent Python limitations, credentials in RAM cannot be
safely shredded. That is, as long as the Python process lives, and even
thereafter, plaintext credentials may exist in RAM until they are overwritten
by another process.
"""

import os
import tempfile

from abc import ABC, abstractmethod
import inspect
import gc

from enum import Enum
import re

from ctlbase.config import Caller
from ctlbase.message import Severity, ErrorMessage
from ctlbase.interaction import Interaction

try:
    import keyring
except ModuleNotFoundError:
    # If keyring is not available, passwords cannot be read from libsecret/Seahorse.
    pass
except Exception as error:
    print("Error: %s (%s)\n" % (str(error), type(error)))


CREDENTIALS_DIRECTORY_DEFAULT = '/run/reader'
""" """


class CredentialKey(Enum):
    """Common keys for a set of credentials in their `dict` representation."""

    USERNAME = 'username'
    LOGIN = USERNAME
    LOGINNAME = USERNAME
    PASSWORD = 'password'
    CRYPTOPHRASE = 'cryptophrase'
    TOTP = 'totp'


class CredentialsHelper:
    @staticmethod
    def parseNetrc(netrcStr, hostname, username=None):
        """Returns a `dict` with up to two keys: LOGIN and PASSWORD"""

        credentialsDict = {}
        if username:
            credentialsDict[CredentialKey.LOGIN] = username

        matches = re.findall(
            r'^machine\s+%s\s+login\s+\"?(\S+)\"?\s+password\s+\"?(.*)(?<!\\)\"?' % hostname if hostname else '\\S+',
            netrcStr, flags=re.MULTILINE
        )

        for groups in matches:
            usernameCandidate = groups[0]
            passwordCandidate = groups[1]

            if username and usernameCandidate != username:
                continue

            credentialsDict[CredentialKey.LOGIN] = usernameCandidate
            credentialsDict[CredentialKey.PASSWORD] = passwordCandidate

        if CredentialKey.PASSWORD not in credentialsDict and hostname:
            if re.search(r'machine.+?login.+?password', netrcStr):
                raise ErrorMessage(
                    "Contents seems to be in netrc format, but target host %s was not found." % hostname,
                    'CREDENTIALS_FORMAT_INVALID', (hostname,)
                )

        return credentialsDict

class BaseReader(ABC):
    """Base class for credential readers."""

    def __init__(
        self,
        credentialKeys=None,
        msgBuffer=None,
        ns=None
    ):
        """Creates a new credential reader.
        
        :param credentialKeys: The credential keys supported by the reader, as a
            subset of :class:`~.CredentialKey`.
        :type credentialKeys: list[CredentialKey] | tuple[CredentialKey]
        :param msgBuffer: A message buffer for appending all occurring messages.
            May be `None` if the reader does not append any messages.
        :type msgBuffer: str
        :param ns: The message namespace to use when appending messages to the
            message buffer. 
            May be `None` if the reader does not append any messages.
        :type ns: str
        """

        # Assume default credentialKeys if none are given.
        if not credentialKeys:
           credentialKeys = (CredentialKey.USERNAME, CredentialKey.PASSWORD)

        self.credentialKeys: list[CredentialKey] | tuple[CredentialKey] = credentialKeys
        """The credential keys supported by the reader, as a subset of
        :class:`~.CredentialKey`.
        """

        self.msgBuffer = msgBuffer
        self.ns = ns

    def credentialKeysOtherThanLogin(self):
        """
        :return: All the keys in :attr:`~.BaseReader.credentialKeys` other than the
            login / user name.
        :rtype: list[CredentialKey]
        """

        remainingCredentialKey = []
        for dictKey in self.credentialKeys:
            if dictKey not in (CredentialKey.USERNAME, CredentialKey.LOGIN, CredentialKey.LOGINNAME):
                remainingCredentialKey.append(dictKey)
        return remainingCredentialKey

    async def readCredential(self, mayPrompt=None, purpose=None):
        """Reads a set of credentials.
        
        Access to the credential source is delegated to 
        :meth:`~.BaseReader._readCredential`.
        
        :param mayPrompt: If `True`, the user may be prompted interactively for
            missing credentials. If `False`, an exception is raised in the case
            of missing credentials.
        :type mayPrompt: bool
        :param purpose: A language-agnostic `str` to present to the user as the
            purpose of the requested credentials. Example: ``HomeServer via SMB``
            Only relevant when interactively prompting the user for credentials.
        :type purpose: str
        
        :raise ValueError: If the `dict` returned by 
            :meth:`~.BaseReader._readCredential` does not contain all the keys
            listed in :attr:`~.BaseReader.credentialKeys`.
        :raise ValueError: If arguments provided in the constructor turn out to
            be invalid before actually accessing the source.
        :raise ErrorMessage: If the credentials could not be read, apart from 
            invalid arguments.
        
        :return: A `dict` containing the keys listed in
            :attr:`~.BaseReader.credentialKeys`, and the corresponding values read
            from the source.
        :rtype: dict[CredentialKey, str]
        """

        # Propagate purpose argument to _readCredential(), if available.
        kwargs = {}
        if mayPrompt is not None and \
        'mayPrompt' in inspect.signature(self._readCredential).parameters:
            kwargs['mayPrompt'] = mayPrompt
        if purpose is not None and \
        'purpose' in inspect.signature(self._readCredential).parameters:
            kwargs['purpose'] = purpose

        credentialsDict = await self._readCredential(**kwargs)
        
        # Check presence of requested credentialKeys.
        for dictKey in self.credentialKeys:
            if dictKey not in credentialsDict:
                # In the case of a login / user name, retrieve the value 
                # from a member variable of the same name, if it exists.
                if dictKey in (
                    CredentialKey.USERNAME,
                    CredentialKey.LOGIN,
                    CredentialKey.LOGINNAME
                ) and hasattr(self, dictKey.value):
                    credentialsDict[dictKey] = getattr(self, dictKey.value)
                else:
                    raise ValueError("%s key could not be retrieved." % dictKey)

        return credentialsDict

    @abstractmethod
    async def _readCredential(self, mayPrompt=None, purpose=None) -> dict[CredentialKey, str]:
        """Implements the logic for reading a set of credentials from an actual
        source.
        
        The parameters correspond to 
        :paramref:`~.readCredential.mayPrompt` and
        :paramref:`~.readCredential.purpose`, respectively. Both are optional
        and may be omitted in the signature.
        
        :return: A `dict` containing the keys listed in
            :attr:`~.BaseReader.credentialKeys`, and the corresponding values read
            from the source.
        :rtype: dict[CredentialKey, str]
        """

        raise NotImplementedError


class FileReader(BaseReader):
    """A credential reader based on text files. The file must be in netrc format
    or contain a single line with the credential.
    
    The reader's :meth:`~.BaseReader.readCredential` method returns a `dict`
    with the following keys:
    
    * :attr:`~.CredentialKey.USERNAME`
    * :attr:`~.CredentialKey.PASSWORD`, unless :paramref:`~.FileReader.credentialKeys`
      lists another key instead.
    
    Furthermore, the method may raise an instance of :class:`~.ErrorMessage` 
    with one of the following message keys:
    
    * `CREDENTIALS_NOT_FOUND` – If the input file does not exist or is not 
      readable to the current user.
    * `CREDENTIALS_FORMAT_INVALID` – If the input file is in netrc format but 
      the given host name could not be found.
    """

    def __init__(
        self,
        inputFilePath,
        hostname=None,
        username=None,
        credentialKeys=None
    ):
        """
        :param inputFilePath: The path to the input file from which to read the 
            password.
        :type inputFilePath: str
        :param hostname: The hostname of the target entry. Required if the input
            file is in netrc format.
        :type hostname: str
        :param username: The user name of the target entry. Required if the input
            file is in netrc format. Regardless of the input file format, the
            user name is returned in the `dict` from 
            :meth:`~.BaseReader.readCredential` under the 
            :attr:`~.CredentialKey.USERNAME` key.
        :type username: str
        :param credentialKeys: See :paramref:`~.BaseReader.credentialKeys`.
        :type credentialKeys: list[CredentialKey] | tuple[CredentialKey]
        """

        if credentialKeys is None:
            credentialKeys = (CredentialKey.USERNAME, CredentialKey.PASSWORD)

        super().__init__(credentialKeys, msgBuffer=None, ns=None)

        self.inputFilePath = inputFilePath
        self.hostname = hostname
        self.username = username

    async def _readCredential(self, mayPrompt=None):
        if not os.path.isfile(self.inputFilePath) or not os.access(self.inputFilePath, os.R_OK):
            raise ErrorMessage(
                "File %s does not exist or is not readable for the current user." % self.inputFilePath,
                'CREDENTIALS_NOT_FOUND', (self.inputFilePath,)
            )

        remainingKeys = self.credentialKeysOtherThanLogin()
        if len(remainingKeys) != 1:
            raise ValueError("FileReader can only retrieve one credential (other than login / user name).")

        credentialsDict = {}

        with open(self.inputFilePath) as file:
            # file.readinto(bytearray) may be used to be able to overwrite the contents in memory.
            fileContents = file.read().strip()
            credentialsDict = CredentialsHelper.parseNetrc(fileContents, self.hostname, self.username)

        if CredentialKey.PASSWORD not in credentialsDict:
            credentialsDict[CredentialKey.PASSWORD] = fileContents

        if remainingKeys[0] != CredentialKey.PASSWORD:
            credentialsDict[remainingKeys[0]] = credentialsDict[CredentialKey.PASSWORD]
            del credentialsDict[CredentialKey.PASSWORD]

        # If the file is not in netrc format, assume that it only contains the password.
        return credentialsDict


class KeyringReader(BaseReader):
    """A credential reader based on the 
    `keyring <https://pypi.org/project/keyring/>`_ package. The keyring backend
    defaults to the 
    `Freedesktop Secret Service <https://specifications.freedesktop.org/secret-service/latest/>`_,
    i.e. the `keyring.backends.libsecret.Keyring` class. `libsecret` must be
    available on the system for that class to work.
    
    The password must be stored under a specific service and user name. The
    following is the basic command to execute on the command line::
    
        keyring -b=keyring.backends.libsecret.Keyring set <service> <username>
    
    The reader's :meth:`~.BaseReader.readCredential` method returns a `dict` 
    with the following keys:
    
    * :attr:`~.CredentialKey.USERNAME`
    * :attr:`~.CredentialKey.PASSWORD`, unless :paramref:`~.KeyringReader.credentialKeys`
      lists another key instead.
    
    Furthermore, the method may raise an instance of :class:`~.ErrorMessage` 
    with one of the following message keys:
    
    * `CREDENTIALS_NOT_FOUND` – If there is no entry in the keyring with the
      given service and user name.
    """
    
    def __init__(
        self,
        keyringService,
        username,
        keyringBackend='keyring.backends.libsecret.Keyring',
        credentialKeys=None
    ):
        """Creates a new credential reader.
        
        :param keyringService: The service name under which the password is
            stored in the keyring. Corresponds to ``<service>`` in
            the basic command above.
        :type keyringService: str
        :param username: The user name under which the password is stored in
            the keyring. Corresponds to ``<username>`` in the basic command
            above. The user name is also included in the `dict` returned by
            :meth:`~.BaseReader.readCredential` under the 
            :attr:`~.CredentialKey.USERNAME` key.
        :type username: str
        :param keyringBackend: The keyring backend to use if deviating from the
            default.
        :type keyringBackend: keyring.backend.KeyringBackend
        :param credentialKeys: See :paramref:`~.BaseReader.credentialKeys`.
        :type credentialKeys: list[CredentialKey] | tuple[CredentialKey]
        """

        if not keyringService:
            raise ValueError("keyringService must be given to look up a keyring entry.")
        if username is None:
            raise ValueError("username must be given to look up a keyring entry.")

        if credentialKeys is None:
            credentialKeys = (CredentialKey.USERNAME, CredentialKey.PASSWORD)

        super().__init__(credentialKeys, msgBuffer=None, ns=None)

        self.keyringService = keyringService
        self.username = username

        if keyringBackend:
            os.environ['PYTHON_KEYRING_BACKEND'] = keyringBackend
        self.keyringBackend = keyringBackend

    async def _readCredential(self, mayPrompt=None):
        remainingKeys = self.credentialKeysOtherThanLogin()
        if len(remainingKeys) != 1:
            raise ValueError("KeyringReader can only retrieve one credential (other than user / login name).")

        try:
            credential = keyring.get_password(self.keyringService, self.username)
        except RuntimeError as error:
            if "libsecret" in str(error):
                raise ModuleNotFoundError("libsecret cannot be found. If you are in a Python venv, symlink the system's gi package into it.")
            raise error

        if credential is None:
            raise ErrorMessage(
                "Error retrieving password from keyring. Please create an entry with: keyring -b=%s set '%s' '%s'" % (self.keyringBackend, self.keyringService, self.username),
                'CREDENTIALS_NOT_FOUND',
                (self.keyringBackend, self.keyringService, self.username)
            )

        return {CredentialKey.USERNAME: self.username, remainingKeys[0]: credential}


class Tpm2Reader(BaseReader):
    """A credential reader accessing the TPM2 on the local machine. It uses the
    binaries from `tpm2-tools <https://tpm2-tools.readthedocs.io/en/latest/>`_, 
    e.g. `tpm2_unseal`.
        
    The reader's :meth:`~.BaseReader.readCredential` method returns a `dict`
    with the following keys:
    
    * :attr:`~.CredentialKey.USERNAME`
    * :attr:`~.CredentialKey.PASSWORD`, unless :paramref:`~.Tpm2Reader.credentialKeys`
      lists another key instead.
    """

    PCR_AUTH_DEFAULT = 'pcr:sha256:0,2,3,7'
    """ """

    def __init__(
        self,
        commandExecutor,
        tpmHandle,
        tpmAuth=None,
        username=None,
        credentialKeys=None
    ):
        """Creates a new credential reader.
        
        :param commandExecutor: The command executor for `tpm2-tools` binaries.
        :type commandExecutor: CommandExecutor
        :param tpmHandle: Handle for the TPM object that represents the 
            password. Example: ``0x81000041``
        :type tpmHandle: str
        :param tpmAuth: Hash and PCR register specfication for authorizing the 
            access to the password. Takes the form 
            ``<hash algorithm>:<PCR numbers>`` where ``<PCR numbers>`` is a
            comma-separated list of numbers from `0` through `23`. Example:
            ``sha256:0,2,3,7``
            If `None`, the value of :attr:`~.Tpm2Reader.PCR_AUTH_DEFAULT` is
            assumed.
        :type tpmAuth: str
        :param username: The user name to include in the `dict` returned by
            :meth:`~.BaseReader.readCredential` under the 
            :attr:`~.CredentialKey.USERNAME` key. This is a mere pass-through as no
            user name is read from the TPM2.
        :type username: str
        :param credentialKeys: See :paramref:`~.BaseReader.credentialKeys`.
        :type credentialKeys: list[CredentialKey] | tuple[CredentialKey]
        """

        if not isinstance(tpmHandle, str) or not tpmHandle.startswith('0x'):
            raise ValueError("tpmHandle must be a str starting with '0x' specifying the TPM object respresenting the password.")
        if tpmAuth is not None and not isinstance(tpmAuth, str):
            raise ValueError("tpmAuth must be a str specifying a method, a hash algorithm and TPM PCRs, e.g. 'pcr:sha256:0,2,3,7'.")

        if credentialKeys is None:
            credentialKeys = (CredentialKey.USERNAME, CredentialKey.PASSWORD)

        super().__init__(credentialKeys, msgBuffer=None, ns=None)

        self.commandExecutor = commandExecutor
        self.tpmHandle = tpmHandle
        self.tpmAuth = Tpm2Reader.PCR_AUTH_DEFAULT if tpmAuth is None else tpmAuth
        self.username = username

    async def _readCredential(self, mayPrompt=None):
        remainingKeys = self.credentialKeysOtherThanLogin()
        if len(remainingKeys) != 1:
            raise ValueError("KeyringReader can only retrieve one credential (other than user/login name).")

        command = ['sudo', '--user=root', 'tpm2_unseal', '-c', self.tpmHandle, '--auth', self.tpmAuth]
        if isinstance(mayPrompt, bool) and not mayPrompt:
            command.insert(1, '--non-interactive')

        credential = await self.commandExecutor.executeCommands(
            command, isSynchronous=True, isLogEnabled=False
        )

        return {CredentialKey.USERNAME: self.username, remainingKeys[0]: credential}


class PromptReader(BaseReader):
    """A credential reader prompting the user for a credential on the command
    line.
    
    The reader's :meth:`~.BaseReader.readCredential` method returns a `dict` 
    with the following message keys:
    
    * :attr:`~.CredentialKey.USERNAME`
    * :attr:`~.CredentialKey.PASSWORD`, unless :paramref:`~.PromptReader.credentialKeys`
      lists another key instead.
    
    Furthermore, the method may raise an instance of :class:`~.ErrorMessage` 
    with one of the following message keys:
    
    * `CREDENTIALS_PROMPT_SUPPRESSED` – If user prompts are suppressed, i.e. 
      :paramref:`~.BaseReader.readCredential.mayPrompt` is `False`.
    * `CREDENTIALS_PROMPT_ABORTED` – If entering the credential was aborted by
      the user.
    """
    
    def __init__(
        self, 
        username=None, 
        purpose=None,
        credentialKeys=None
    ):
        """Creates a new credential reader.
        
        :param username: The user name to include in the `dict` returned by
            :meth:`~.BaseReader.readCredential` under the 
            :attr:`~.CredentialKey.USERNAME` key. Unless 
            :paramref:`~.PromptReader.purpose` is specified, the user name
            is included in the prompt presented to the user.
        :type username: str
        :param purpose: A language-agnostic `str` to present to the user as the 
            purpose of the requested credentials. Example: ``HomeServer via SMB``
        :type purpose: str
        :param credentialKeys: See :paramref:`~.BaseReader.credentialKeys`.
        :type credentialKeys: list[CredentialKey] | tuple[CredentialKey]
        """

        if credentialKeys is None:
            credentialKeys = (CredentialKey.USERNAME, CredentialKey.PASSWORD)

        super().__init__(credentialKeys, msgBuffer=None, ns=None)

        self.username = username
        self.purpose = purpose

    async def _readCredential(self, mayPrompt=None, purpose=None):
        remainingKeys = self.credentialKeysOtherThanLogin()
        if len(remainingKeys) != 1:
            raise ValueError("KeyringReader can only retrieve one credential (other than user/login name).")

        if not (mayPrompt is None or mayPrompt):
            raise ErrorMessage("May not prompt for credential, but credential is required.", 'CREDENTIALS_PROMPT_SUPPRESSED')

        if purpose is None:
            purpose = self.purpose

        if purpose is None:
            purpose = self.username
        elif self.username:
            purpose = '%s (%s)' % (purpose, self.username)

        credential = Interaction.promptForCredential(purpose)

        if credential is None:
            raise ErrorMessage("credential request aborted.", 'CREDENTIALS_PROMPT_ABORTED')

        return {CredentialKey.USERNAME: self.username, remainingKeys[0]: credential}

    

class CredentialReader(BaseReader):
    """A universal credential reader backed by an instance of one of the
    following classes:
    
    * :class:`~.KeyringReader`
    * :class:`~.Tpm2Reader`
    * :class:`~.FileReader`
    
    An instance of :class:`~.PromptReader` is always created as a fallback
    reader.
        
    This class is helpful to read credentials from one of various sources, based
    on values determined at runtime. For instance, a control can pass its
    environment configuration values to :meth:`~.CredentialReader.__init__`, 
    dynamically creating a backing credential reader.
    
    The reader's :meth:`~.BaseReader.readCredential` method may raise instances
    of :class:`~.ErrorMessage` with the following message keys:
    
    * `CREDENTIALS_FALLING_BACK` – If the backing credential reader failed and
      the fallback reader is used.
    * All the message keys from the backing credential reader.
    """
    
    class TypeToArgs(Enum):
        """Maps the names of keyword arguments to corresponding credential 
        reader classes.
        """

        FileReader = ('inputFilePath',)
        KeyringReader = ('keyringService', 'service')
        Tpm2Reader = ('tpmHandle', 'tpmAuth')

    def __init__(self, **kwargs):
        """Creates a new credential reader.
        
        :param kwargs: Based on the following keyword arguments, the class of
            the backing credential reader is determined and instantiated:
            
            * :paramref:`~.KeyringReader.keyringService` or **service** (`str`): 
              Required for :class:`~.KeyringReader`.
            * :paramref:`~.Tpm2Reader.commandExecutor` (`CommandExecutor`): 
              Required for :class:`~.Tpm2Reader`.
            * :paramref:`~.Tpm2Reader.tpmHandle` (`str`): Required for 
              :class:`~.Tpm2Reader`.
            * :paramref:`~.Tpm2Reader.tpmAuth` (`str`): Optional for 
              :class:`~.Tpm2Reader`.
            * :paramref:`~.FileReader.inputFilePath` (`str`): Required for 
              :class:`~.FileReader`.
            * :paramref:`~.FileReader.hostname` (`str`): Optional for 
              :class:`~.FileReader`, depending on the use case.
            * :paramref:`~.KeyringReader.username` (`str`): Required for 
              :class:`~.KeyringReader`. Optional for :class:`~.Tpm2Reader`, 
              :class:`~.FileReader` and :class:`~.PromptReader`, depending 
              on the use case.
            * :paramref:`~.PromptReader.purpose` (`str`): Optional for
              :class:`~.PromptReader`.
            * :paramref:`~.BaseReader.credentialKeys` (`dict`), 
              :paramref:`~.BaseReader.msgBuffer` (`MessageBuffer`) and 
              :paramref:`~.BaseReader.ns` (`str`): Optional for all classes.
        :type kwargs: dict
        
        :raise ValueError: If the class for the backing credential reader could
            not be determined unambiguously.
        """

        for kwarg in (
            'commandExecutor', 
            'inputFilePath', 'hostname', 'keyringService', 'service',
            'tpmHandle', 'tpmAuth', 'username', 'purpose', 'msgBuffer',
            'credentialKeys', 'ns'
        ):
            if kwarg in kwargs:
                setattr(self, 'keyringService' if kwarg == 'service' else kwarg, kwargs[kwarg])
            else:
                setattr(self, kwarg, None)

        # Check mutually exclusive arguments.
        selectedArgs = None
        for candidateArgs in CredentialReader.TypeToArgs:
            for arg in candidateArgs.value:
                if getattr(self, arg) is not None:
                    if not selectedArgs:
                        selectedArgs = candidateArgs
                        break
                    else:
                        raise ValueError("Arguments %s may not be combined with %s." % ('/'.join(selectedArgs.value), '/'.join(args.value)))

        self.fallbackReader = PromptReader(self.username, self.purpose)

        if selectedArgs == CredentialReader.TypeToArgs.FileReader:
            self.reader = FileReader(
                self.inputFilePath, hostname=self.hostname, 
                username=self.username, credentialKeys=self.credentialKeys
            )
        elif selectedArgs == CredentialReader.TypeToArgs.KeyringReader:
            self.reader = KeyringReader(
                self.keyringService, self.username, credentialKeys=self.credentialKeys
            )
        elif selectedArgs == CredentialReader.TypeToArgs.Tpm2Reader:
            if not hasattr(self, 'commandExecutor'):
                raise ValueError("commandExecutor is required for reading a credential from TPM.")

            self.reader = Tpm2Reader(
                self.commandExecutor, self.tpmHandle, tpmAuth=self.tpmAuth,
                username=self.username, credentialKeys=self.credentialKeys
            )
        else:
            self.reader = self.fallbackReader

        super().__init__(
            self.reader.credentialKeys if self.credentialKeys is None else self.credentialKeys,
            Caller.getName().upper() if (isinstance(self.ns, bool) and self.ns) else self.ns
        )

        if self.msgBuffer is not None and self.msgBuffer.nsDefault is None:
            self.msgBuffer.nsDefault = self.ns

    async def _readCredential(self, mayPrompt=None, purpose=None):
        try:
            kwargs = {}
            if mayPrompt is not None and \
            'mayPrompt' in inspect.signature(self.reader._readCredential).parameters:
                kwargs['mayPrompt'] = mayPrompt
            if purpose is not None and \
            'purpose' in inspect.signature(self.reader._readCredential).parameters:
                kwargs['purpose'] = purpose

            return await self.reader._readCredential(**kwargs)
        except (NameError, ModuleNotFoundError) as error:
            raise error
        except Exception as error:
            if self.reader is self.fallbackReader:
                raise error

            if self.msgBuffer is not None:
                self.msgBuffer.append(
                    "%s. Falling back to interactive prompt." % str(error),
                    'CREDENTIALS_FALLING_BACK', severity=Severity.DEBUG,
                    ns=self.ns
                )

            kwargs = {}
            if mayPrompt is not None and \
            'mayPrompt' in inspect.signature(self.fallbackReader._readCredential).parameters:
                kwargs['mayPrompt'] = mayPrompt
            if purpose is not None and \
            'purpose' in inspect.signature(self.fallbackReader._readCredential).parameters:
                 kwargs['purpose'] = purpose
            return await self.fallbackReader._readCredential(**kwargs)


class TemporaryCredentials:
    """An async context manager temporarily providing a set of credentials. When
    entering, credentials are read from a credential reader and written to a 
    temporary file in one of the formats listed in 
    :class:`~.TemporaryCredentials.Format`. When exiting, the temporary file is
    deleted again unless :attr:`~.TemporaryCredentials.delete` is set to `False`.
    
    Example usage::
    
        tmp = TemporaryCredentials(
            '/tmp/smb', 
            PromptReader('hugo', 'HomeServer via SMB'), 
            format=TemporaryCredentials.Format.SMB
        )
        
        async with tmp:
            with open(tmp.path) as file:
                print(file.read())
    
    When entering the context manager, an instance of :class:`~.ErrorMessage` 
    may be raised with one the following message keys:
    
    * `CREDENTIALS_ALL_FAILED` – If all credential readers in 
      :paramref:`~.TemporaryCredentials.readers` failed.
    """
    
    class Format(Enum):
        """Supported output formats for credentials."""

        SMB = 'username=%s\npassword=%s\n'
        """SMB / CIFS mount format, i.e. ``username=`` on the first line and 
        ``password=`` on the second.
        """

        DAVFS = '%s %s %s\n'
        """DAVFS2 format, i.e. a single line with the mountpoint, the user name
        and password, separated by whitespace.
        """

        DAVFS2 = DAVFS
        """An alias for `DAVFS`."""

        BASH_LITERAL = '%s'
        """A bash literal with an associative array, containing one or more 
        keys from :class:`~.CredentialKey` with the corresponding values, e.g.
        ``(['username']='hugo' ['password']='q3o##3jHSNTxw~cr')``.
        """

        PLAIN = '%s\n'
        """A single plaintext password."""

    def __init__(
        self,
        path,
        readers,
        format=Format.PLAIN,
        mountpoint=None,
        delete=True,
        msgBuffer=None,
        ns=True,              # 'ns' is i18next wording; True for auto-discovery.
        mayPrompt=None,
        purpose=None
    ):
        """Creates a new context manager for a set of credentials. At his point,
        credentials are neither read nor written. This only happens when 
        entering the context manager.
        
        :param path: The path under which the temporary file is expected.
            If only a file name is given, without any path specification, the
            value of :data:`~.CREDENTIALS_DIRECTORY_DEFAULT` is assumed.
            If `None`, the temporary file can only be accessed via 
            :attr:`~.TemporaryCredentials.namedTemp`.
        :type path: str
        :param readers: The credential readers to be called in sequence to
            actually read the credentials. If the first one fails, the second
            is called and so on until one of them succeeds. For every failing
            reader, its error message is appended to the message buffer.
        :type readers: list[BaseReader] | tuple[BaseReader] | BaseReader
        :param format: See :attr:`~.TemporaryCredentials.format`.
        :type format: TemporaryCredentials.Format
        :param mountpoint: See :attr:`~.TemporaryCredentials.mountpoint`.
        :type mountpoint: str
        :param delete: See :attr:`~.TemporaryCredentials.delete`.
        :type delete: bool
        :param msgBuffer: The message buffer to which all occurring messages are
            appended. It is propagated to all credential readers given in
            :paramref:`~.TemporaryCredentials.readers` unless they have a 
            message buffer of their own.
        :type msgBuffer: MessageBuffer
        :param ns: The message namespace to use when appending messages to the
            message buffer. If `True`, the namespace is derived from the caller
            name. See :class:`~.CallerInspector` for the discovery algorithm.
            The namespace is propagated to all credential readers given in
            :paramref:`~.TemporaryCredentials.readers` unless they have a
            message namespace of their own. The default namespace of the message
            buffer is left unchanged. This allows for a separate namespace
            helpful for security audits and debugging.
        :type ns: str | True | None
        :param mayPrompt: See :attr:`~.TemporaryCredentials.mayPrompt`.
        :type mayPrompt: bool
        :param purpose: See :attr:`~.TemporaryCredentials.purpose`.
        :type purpose: str
        """

        if path:
            self.dirPath = os.path.dirname(path) if '/' in path else CREDENTIALS_DIRECTORY_DEFAULT.rstrip('/')
            self.path = path
        else:
            self.dirPath = None
            self.path: str = None
            """The fixed path to which the randomly named temporary file is
            hardlinked.
            """

        if isinstance(readers, list) or isinstance(readers, tuple):
            self.readers = readers
        elif readers is not None:
            self.readers = [readers]
        else:
            self.readers = None

        self.format: TemporaryCredentials.Format = format
        """The format in which credentials are written to the temporary file."""

        self.mountpoint: str = mountpoint
        """The DAVFS mounpoint for which the credentials are valid. Only
        relevant when using the :attr:`~.TemporaryCredentials.Format.DAVFS` 
        format.
        """

        self.delete: bool = delete
        """| If `True`, the temporary credentials file is auto-deleted when the
          context manager exists.
        | The attribute name is borrowed from Python's 
          :external:func:`tempfile.NamedTemporaryFile` class.
        | **Note**: Set to `False` only for debugging purposes!
        """

        self.mayPrompt: bool = mayPrompt
        """If `True`, the user may be prompted interactively for credentials."""

        self.purpose: str = purpose
        """A language-agnostic `str` to present to the user as the purpose of
        the requested credentials. Example: ``HomeServer via SMB``. Only
        relevant when interactively prompting the user for credentials.
        """

        if isinstance(ns, bool) and ns:
            self.ns = Caller.getName().upper()
        else:
            self.ns = ns
        
        if self.ns is not None and self.readers:
            for reader in self.readers:
                if reader.ns is None:
                    reader.ns = self.ns

        self.msgBuffer = msgBuffer
        if self.msgBuffer is not None and self.msgBuffer.nsDefault is None:
            self.msgBuffer.nsDefault = self.ns

        if self.msgBuffer is not None and self.readers:
            for reader in self.readers:
                if reader.msgBuffer is None:
                    reader.msgBuffer = self.msgBuffer

        self.namedTemp: str = None
        """The path to the randomly named temporary file to which the
        credentials are written.
        """

    async def __aenter__(self):
        if not self.path or not self.readers:
            return self

        credentialsDict = {}
        for reader in self.readers:
            kwargs = {'mayPrompt': self.mayPrompt}
            if self.purpose is not None:
                kwargs['purpose'] = self.purpose
            try:
                credentialsDict.update(await reader.readCredential(**kwargs))
                break
            except ErrorMessage as errorMsg:
                self.msgBuffer.append(errorMsg)
            except Exception as error:
                self.msgBuffer.append(
                    "Error reading credential with %s: %s" % (type(reader).__name__, str(error)),
                    'CREDENTIALS_READER_FAILED', (type(reader), error), 
                    Severity.ERROR, ns=self.ns
                )

        if not credentialsDict:
            raise ErrorMessage(
                "Error reading credential with all the given readers.",
                'CREDENTIALS_ALL_FAILED', (), ns=self.ns
            )

        self.namedTemp = tempfile.NamedTemporaryFile(
            mode='w', buffering=1, encoding='utf-8', 
            delete=False, dir=self.dirPath
        )

        with self.namedTemp as file:
            if self.format == TemporaryCredentials.Format.PLAIN:
                file.write(self.format.value % (credentialsDict[CredentialKey.PASSWORD],))
            elif self.format == TemporaryCredentials.Format.SMB:
                file.write(self.format.value % (credentialsDict[CredentialKey.USERNAME], credentialsDict[CredentialKey.PASSWORD]))
            elif self.format in (TemporaryCredentials.Format.DAVFS, TemporaryCredentials.Format.DAVFS2):
                file.write(self.format.value % (self.mountpoint, credentialsDict[CredentialKey.USERNAME], credentialsDict[CredentialKey.PASSWORD]))
            elif self.format == TemporaryCredentials.Format.BASH_LITERAL:
                file.write("(")
                index = -1
                for key, value in credentialsDict.items():
                    index += 1
                    value = str(value).replace("'", "\"'\"")
                    file.write("['")
                    if isinstance(key, Enum):
                        file.write(key.value)
                    else:
                        file.write(str(key))
                    file.write("']='")
                    file.write(value)
                    file.write("'")
                    if index < len(credentialsDict) - 1:
                        file.write(" ")
                file.write(")")

        del credentialsDict
        gc.collect()

        if os.path.lexists(self.path):
            os.remove(self.path)
        # As the temporary file name is random, create a hardlink with the given name.
        # By contrast, a symlink does not seem to be accessible by root or any other than the creating user.
        os.link(self.namedTemp.name, self.path)
        
        return self

    async def deleteCredential(self):
        """Deletes the temporary file :attr:`~.namedTemp` along with its 
        hardlink :attr:`~.path`.
        
        Unless :attr:`~.delete` is set to `False`, this method is called
        automatically when the context manager exits.
        
        :return: `True` if a temporary file existed and was deleted. `False` if
            no temporary file exists.
        :rtype: bool
        """

        isDeleted = False
        try:
            if self.path and os.path.lexists(self.path):
                os.remove(self.path)
                isDeleted = True
        finally:
            if self.namedTemp and os.path.exists(self.namedTemp.name):
                os.remove(self.namedTemp.name)
                isDeleted = True
        return isDeleted

    async def __aexit__(self, errorType, error, traceback):
        if error is not None:
          return False

        if self.delete:
            return await self.deleteCredential()
        

