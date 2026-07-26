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


import sys
import os

import inspect

from abc import ABC, abstractmethod

import copy
from enum import Enum
from io import StringIO
import re

from ctlbase.config import TRUE_VALUE_PATTERN, FALSE_VALUE_PATTERN, Caller, BashHelper
from ctlbase.message import Severity, ErrorMessage, MessageHelper, LoggingMessageBuffer
from ctlbase.mode import TIMEOUT_DEFAULT_S
from ctlbase.process import CleaningUp, CommandExecutor


MODE_DIR_NAME_ENVKEY = 'MODE_PATH'
"""Environment configuration key for the mode directory."""

MODE_GROUP_ENVKEY = 'MODE_GROUP'
"""Environment configuration key for the group ownership of the mode directory."""

MODE_TIMEOUT_DEFAULT_ENVKEY = 'MODE_TIMEOUT_DEFAULT_S'
"""Environment configuration key for the default pending mode timeout in seconds."""

ALREADY_OK_SEVERITY = Severity.NORMAL
"""The severity to use when the target state of an action is already reached
and, under the current circumstances, is considered "OK". The value is in effect
for the entire Python process and is normally one of :attr:`~.Severity.SUCCESS`,
:attr:`~.Severity.NORMAL`, :attr:`~.Severity.VERBOSE` or :attr:`~.Severity.DEBUG`.
"""

ALREADY_NOT_OK_SEVERITY = Severity.WARNING
"""The severity to use when the target state of an action is already reached
and, under the current circumstances, is considered "not OK". The value is in
effect for the entire Python process and is normally one of
:attr:`~.Severity.WARNING` or :attr:`~.Severity.ERROR`.
"""


class Control():
    """Base class for a control.
    
    It provides a uniform Python interface for various kinds of controls. Among 
    the benefits, it is easy to build CLI, REST, web and other user interfaces 
    on top. What is more, composite controls for multiple resources can be 
    created, preserving the uniform interface.
    
    Subclasses must comply with the following rules:
    
        * The super constructor :meth:`~.Control.__init__` must be called.
        * :meth:`~.Control._performAction` must be implemented if there is at
          least one custom action the control can perform.
        * :meth:`~.Control._getProperty` and :meth:`~.Control._getPropertiesListsEn` 
          must be implemented if there is at least one custom property the 
          control can provide.
    """

    def __init__(
        self,
        loop,
        commandExecutor=None,
        envConfig=None,
        actionType=None,
        modeType=None,
        isSkipPendingMode=False,
        propertyType=None,
        msgBuffer=None,
        ns=True,
        jsonIndent=4
    ):
        """
        :param commandExecutor: A process executor for executing commands in a
            subprocess. `None` if the control does not need to execute commands.
        :type commandExecutor: :class:`~.CommandExecutor` | None
        :param envConfig: An environment configuration in one of the following 
            forms:
            
            * A tuple of an absolute file path and a `dict` representing the
              parsed configuration.
            * The file name of the Bash-style configuration file to be parsed.
              It may include a relative or absolute path specification.
              See :meth:`~.FileHelper.find` for the discovery algorithm.
            * `True` for auto-discovering a configuration file based on the 
              caller's normalized module name as returned by 
              :meth:`~.Caller.getName`.
              See :meth:`~.FileHelper.find` for the discovery algorithm.
            * `None` if the control does not need an environment configuration.
            
            | See :class:`~.BashHelper` for the supported configuration syntax.
            | A possibly existing :data:`~.MODE_DIR_NAME_ENVKEY` environment 
              value is used for discovering the mode directory.
              This is only relevant if :paramref:`~.Control.modeType` is not 
              `None`.
        :type envConfig: tuple[str, dict] | str | bool | None
        :param actionType: The action `Enum` class.
        :type actionType: Enum
        :param modeType: The mode `Enum` class. `None` if the control does not
            support different modes.
        :type modeType: Enum | None
        :param isSkipPendingMode: If `True`, the
            :attr:`~.StandardAction.MODE_SET` and
            :attr:`~.StandardAction.MODE_UNSET` actions always related to the
            active mode. The pending mode is skipped. Only relevant if
            :paramref:`~.Control.modeType` is not `None`.
        :type isSkipPendingMode: bool
        :param propertyType: The property `Enum` class. `None` if the control 
            does not support any property.
        :type propertyType: Enum | None
        :param msgBuffer: The message buffer to which all occurring messages are
            appended. `True` for auto-creation.
        :type msgBuffer: :class:`~.MessageBuffer` | True
        :param ns: The message namespace to use when writing messages to the
            message buffer. If `True`, the namespace is derived from the 
            caller's normalized module name, as returned by 
            :meth:`~.CallerInspector.getName`. If auto-creating a message
            buffer, the namespace set as its default namespace.
        :type ns: str | True | None
        :param jsonIndent: The number of spaces used to indent one hierarchy
            level in JSON output.
        :type jsonIndent: int
        """

        self.loop = loop
        self.commandExecutor = commandExecutor
        self.jsonIndent = jsonIndent
        
        self.actionType = actionType
        if self.actionType is not None:
            # Add the default methods to the given action `Enum`.
            setattr(self.actionType, '__eq__', StandardAction.__eq__)
            setattr(self.actionType, 'fromAny', classmethod(StandardAction.fromAny.__func__))

        if envConfig is None:
            self.envConfigName, self.envConfigDict = (None, None,)
        elif type(envConfig) in (tuple, list,) and len(envConfig) == 2 and isinstance(envConfig[0], str) and isinstance(envConfig[1], dict):
            self.envConfigName = envConfig[0]
            self.envConfigDict = envConfig[1]
        elif isinstance(envConfig, dict):
            self.envConfigDict = envConfig
        elif (isinstance(envConfig, bool) and envConfig) or type(envConfig) in (str, tuple, list,):
            if type(envConfig) in (tuple, list,):
                envConfig = True
            self.envConfigName, self.envConfigDict = BashHelper.parseConfig(envConfig)
        else:
            raise ValueError("envConfig must represent a path as str or a tuple of path and config dictionary or True for auto-discovery.")

        self.modeType = modeType
        if self.modeType is not None:
            from ctlbase.mode import FileModeHandler

            modePath = None
            if self.envConfigDict is not None and MODE_DIR_NAME_ENVKEY in self.envConfigDict:
                modePath = self.envConfigDict[MODE_DIR_NAME_ENVKEY]
            self.mode = FileModeHandler(modePath if modePath else True)

            # Add the default methods to the given mode `Enum`.
            setattr(self.modeType, '__eq__', StandardMode.__eq__)
            setattr(self.modeType, 'fromAny', classmethod(StandardMode.fromAny.__func__))
        else:
            self.mode = None
        
        self.isSkipPendingMode = isSkipPendingMode

        self.propertyType = propertyType
        if self.propertyType is not None:
            # Add the default methods to the given property `Enum`.
            setattr(self.propertyType, '__eq__', StandardProperty.__eq__)
            setattr(self.propertyType, 'fromAny', classmethod(StandardProperty.fromAny.__func__))

        self.ns = Caller.getName().upper() if isinstance(ns, bool) and ns else ns
        if isinstance(msgBuffer, bool) and msgBuffer:
            self.isOwnMsgBuffer = True
            self.msgBuffer = LoggingMessageBuffer(self.loop, self.ns)
        else:
            self.isOwnMsgBuffer = False
            self.msgBuffer = msgBuffer

        if self.loop is None and self.commandExecutor is not None:
            self.loop = self.commandExecutor.loop

    def _actionToMessageKey(self, action, feedback=None):
        infinitive, presentParticiple, pastParticiple, object, addendum = ActionHelper.getWordForms(action)
        
        key = StringIO()
        
        if object is not None:
            key.write(object)
            key.write('_')

        if feedback == 'already':
            key.write(re.sub(r'[-\s]', '_', pastParticiple))
        else:
            key.write(re.sub(r'[-\s]', '_', infinitive))
        
        if addendum is not None:
            key.write('_')
            key.write(addendum)
        
        if feedback is not None:
            key.write('_')
            key.write(feedback)
        
        return key.getvalue().upper()

    def _actionToGrammarEn(self, action, argument, feedback):
        """Creates a set of English word forms for a given action. 
        
        The control uses these word forms to auto-create English message texts.
        In order to fine-tune message texts, an implementation may override this
        method.
        
        The following examples show the outcomes of the default implementation:
        
        * ``mode-set`` returns ``set``, ``setting``, ``set``, ``mode``, `None`
        * ``power-off`` or ``poweroff`` returns ``power``, ``powering``, ``powered``, `None`, ``off``
        * ``fade-out`` or ``fadeout`` returns ``fade``, ``fading``, ``faded``, `None`, ``out``
        * ``light-fade-out`` or ``light-fadeout`` returns ``fade``, ``fading``, ``faded``, ``light``, ``out``
        
        The arguments to an action and the feedback from an action may influence
        the outcomes. For simplicity, this is not considered in the examples
        above.
        
        :param action: The action for which to create the word forms. Either an
            action `Enum` value (`str`) or the action `Enum` member itself. The 
            value must comply to the rules described under
            :ref:`action <control-action>`.
        :type action: Enum | str
        :param argument: The argument(s) to the action. See
            :paramref:`~.Control.performAction.argument`.
        :type argument: object | list | tuple | None
        :param feedback: A feedback identifier. See
            :ref:`feedback <control-feedback>`.
        :type feedback: str | None
        
        :return: A tuple of five elements:
        
            * **action** (`str`) – The infinitive form of the action verb.
            * **presentParticiple** (`str`) – The present progressive form of
              the action verb.
            * **pastParticiple** (`str`) – The past participle of the action
              verb.
            * **object** (`str` | `None`) – The object of the action, e.g. 
              ``mode`` or ``light``. It may be derived from the action value
              and/or from the argument(s) to the action. If `None`, message
              texts are formulated without an object.
            * **addendum** (`str` | `None`) – An addendum as listed under
              :ref:`action <control-action>`, possibly amended by an adverb
              related to the feedback, e.g. ``down already``. If `None`, message
              texts are formulated without an addendum.
        :rtype: tuple
        """

        infinitive, presentParticiple, pastParticiple, object, addendum = ActionHelper.getWordForms(action)

        # Replace Enum members by their values.
        if isinstance(argument, Enum):
            argument = argument.value
        if isinstance(argument, tuple) or isinstance(argument, list):
            argument = [x.value if isinstance(x, Enum) else x for x in argument]

        if action == StandardAction.MODE_UNSET and argument:
            try:
                object += " " + argument[0]
            except:
                pass
        elif action == StandardAction.MODE_SET:
            try:
                object += " " + argument[0]
            except:
                pass
            if isinstance(argument, tuple) or isinstance(argument, list) and argument[1]:
                addendum = 'for %i seconds' % argument[1]

        if feedback == 'already':
            if addendum:
                addendum += ' already'
            else:
                addendum = 'already'

        return infinitive, presentParticiple, pastParticiple, object, addendum

    def _appendMessageFromActionEn(
        self,
        action,
        argument,
        feedback=None,
        isOkNodo=False,
        correlationId=None
    ):
        """Creates a message for a given action and appends it to the message
        buffer.
        
        If the control does not have a message buffer, nothing happens.
        
        The English message text and the severity are determined by the feedback
        identifier. The severities map as follows:
        
            * ``already`` maps to :data:`~.ALREADY_OK_SEVERITY` or
              :data:`~.ALREADY_NOT_OK_SEVERITY`, depending on the value
              of :paramref:`~.Control._appendMessageFromActionEn.isOkNodo`
            * ``prevented`` maps to :attr:`~.Severity.WARNING`
            * ``disabled`` maps to :attr:`~.Severity.VERBOSE`
            * ``failed`` maps to :attr:`~.Severity.ERROR`
            * all other feedback identifiers map to :attr:`~.Severity.SUCCESS`
        
        The English message text is created using the word forms returned by
        :meth:`~.Control._actionToGrammarEn`.
        
        :param action: The action for which to create a message. Either an
            action `Enum` value (`str`) or the action `Enum` member itself. The 
            value must comply to the rules described under
            :ref:`action <control-action>`.
        :type action: Enum | str
        :param argument: The argument(s) to the action. See
            :paramref:`~.Control.performAction.argument`.
        :type argument: object | list | tuple | None
        :param feedback: A feedback identifier. See
            :ref:`feedback <control-feedback>`.
        :type feedback: str | None
        :param isOkNodo: If `True`, it is considered "OK" when the target state
            of the action is already reached.
        :type isOkNodo: bool | None
        :param correlationId: The correlation ID with which to create the
            message. See :attr:`~.Message.correlationId`.
        :type correlationId: str | None
        """

        if self.msgBuffer is None:
            return

        if feedback is None:
            feedback = 'succeeded'

        infinitive, presentParticiple, pastParticiple, object, addendum = self._actionToGrammarEn(action, argument, feedback)
        msgTextEn = StringIO()

        if feedback in ('already', 'prevented', 'disabled', 'succeeded'):
            participle = pastParticiple
        else:
            participle = presentParticiple

        key = self._actionToMessageKey(action, feedback)
        if feedback == 'already':
            severity = ALREADY_OK_SEVERITY if isOkNodo else ALREADY_NOT_OK_SEVERITY
        elif feedback == 'prevented':
            severity = Severity.WARNING
        elif feedback == 'disabled':
            severity = Severity.VERBOSE
        elif feedback == 'failed':
            severity = Severity.ERROR
        else:
            severity = Severity.SUCCESS

        if feedback == 'prevented':
            msgTextEn.write("Prevented")
            if object:
                msgTextEn.write(" ")
                msgTextEn.write(str(object))
                msgTextEn.write(" from ")
            msgTextEn.write(presentParticiple)
            if addendum:
                msgTextEn.write(" ")
                msgTextEn.write(str(addendum))
        elif feedback == 'disabled':
            msgTextEn.write("Did not ")
            msgTextEn.write(infinitive)
            if object:
                msgTextEn.write(" ")
                msgTextEn.write(str(object))
            msgTextEn.write(" because this feature is not enabled")
        else:
            msgTextEn.write(participle.capitalize())
            if object:
                msgTextEn.write(" ")
                msgTextEn.write(str(object))
            if addendum:
                msgTextEn.write(" ")
                msgTextEn.write(str(addendum))
        if feedback == 'failed':
            msgTextEn.write(" failed")
        msgTextEn.write(".")

        self.msgBuffer.append(
            msgTextEn.getvalue(), key, argument, severity,
            ns=self.ns, correlationId=correlationId
        )

    def _createExceptionFromActionEn(
        self,
        action,
        argument,
        error=None,
        feedback='failed'
    ):
        """A variant of :meth:`~.Control._appendMessageFromActionEn`, creating
        an exception, i.e. an instance of :class:`~.ErrorMessage`.
        
        :param action: The action for which to create an exception. Either an
            action `Enum` value (`str`) or the action `Enum` member itself. The 
            value must comply to the rules described under
            :ref:`action <control-action>`.
        :type action: Enum | str
        :param argument: The argument(s) to the action. See
            :paramref:`~.Control.performAction.argument`.
        :type argument: object | list | tuple | None
        :param error: The original error from which to derive an English message
            text. If `None`, a generic text is created. For errors of type 
            :class:`~.CommandExecutor.SynchronousError`, a specific text is
            created. For all other types, a generic text is created.
        :type error: Exception | None
        :param feedback: A feedback identifier. See
            :ref:`feedback <control-feedback>`. The default of ``failed`` only
            needs to be changed if a more specific feedback identifier is
            required for the error.
        :type feedback: str | None
        
        :return: The created exception.
        :rtype: ErrorMessage
        """

        infinitive, presentParticiple, pastParticiple, object, addendum = self._actionToGrammarEn(action, argument, feedback)
        key = self._actionToMessageKey(action, feedback)

        addendum = ' %s' % addendum if addendum else ''

        if error is not None:
            if isinstance(error, CommandExecutor.SynchronousError):
                if error.returncode == 1:
                    if error.message and 'sudo' in error.message:
                        return ErrorMessage(
                            "Superuser rights are required for %s %s." % (presentParticiple, addendum),
                            errorKey, (error,)
                        )
                return ErrorMessage(
                    "Exit code %s %s%s: %s" % (error.returncode, presentParticiple, addendum, error.message),
                    key, (error.returncode, error.message)
                )
            return ErrorMessage(
                "Error %s%s: %s" % (presentParticiple, addendum, error),
                key, (error,)
            )

        return ErrorMessage("Error %s%s." % (presentParticiple, addendum), key, ())

    async def getPendingMode(self):
        """Returns the pending mode if set.
        
        :return: The pending mode `Enum` member if set, otherwise the `NONE` 
            mode `Enum` member.
        :rtype: Enum
       
        :raise RuntimeError: If the control has not been set up for handling 
            modes.
        """

        if self.mode is None:
            raise RuntimeError("Control does not support modes.")

        return self.modeType.fromAny(self.mode.getPending())

    async def getActiveMode(self):
        """Returns the active mode if set.
        
        :return: The active mode `Enum` member if set, otherwise the `NONE` 
            mode `Enum` member.
        :rtype: Enum
        
        :raise RuntimeError: If the control has not been set up for handling 
            modes.
        """

        if self.mode is None:
            raise RuntimeError("Control does not support modes.")

        return self.modeType.fromAny(self.mode.getActive())

    async def unsetPendingMode(self):
        """Unsets the pending mode if set.
        
        :return: The unset mode `Enum` member or `None` if no mode was unset.
        :rtype: Enum | None
        
        :raise RuntimeError: If the control has not been set up for handling 
            modes.
        """

        if self.mode is None:
            raise RuntimeError("Control does not support modes.")

        modeMember = self.modeType.fromAny(self.mode.getPending())
        if modeMember != StandardMode.NONE:
            self.mode.unsetPending()
            return modeMember
        return None

    async def setPendingMode(self, mode, timeout_s=-1, group=None):
        """Sets the pending mode.
        
        :param mode: The mode to set. Either a mode `Enum` value (`str`) or the
            mode `Enum` member itself.
            Passing the `NONE` mode `Enum` member is equivalent to calling
            :meth:`~.Control.unsetPendingMode`.
        :type mode: str | Enum
        :param timeout_s: The pending mode timeout in seconds. If
            :meth:`~.Control.activatePendingMode` is not called within that
            time span, the pending mode will be unset automatically.
            If ``-1``, the :data:`~.MODE_TIMEOUT_DEFAULT_ENVKEY` environment 
            value is used if it exists. If not, the value of 
            :data:`~.mode.TIMEOUT_DEFAULT_S` is used.
            Passing ``0`` is equivalent to calling 
            :meth:`~.Control.unsetPendingMode`.
        :type timeout_s: int | None
        :param group: The group ownership to be set on the mode directory. 
            Accepts a group name or numeric group ID (gid).
            If `None`, the :data:`~.MODE_GROUP_ENVKEY` environment value is
            used if it exists. If not, the group ownership is left unmodified.
        :type group: str | None
        
        :raises ValueError: If the mode `Enum` does not have a member
            corresponding to :paramref:`~.Control.setMode.mode`.
        :raise RuntimeError: If the control has not been set up for handling 
            modes.
        
        :return: A tuple with the following elements:
        
            * **actualAction** (`Enum`) – The actual action performed, as an 
              action `Enum` member, i.e. `MODE_SET` or `MODE_UNSET`.
            * **mode** (`Enum`) – The mode `Enum` member that was set or unset, 
              respectively.
            * **timeout_s** (`int`) – The timeout in seconds after which the mode 
              will be unset automatically. ``0`` in the case of the `MODE_UNSET`
              action.
        :rtype: tuple
        """

        if self.mode is None:
            raise RuntimeError("Control does not support modes.")

        modeMember = self.modeType.fromAny(mode)

        if modeMember == StandardMode.NONE or timeout_s == 0:
            modeMember = await self.unsetPendingMode()
            try:
                return self.actionType(StandardAction.MODE_UNSET.value), modeMember, 0
            except:
                return StandardAction.MODE_UNSET, modeMember, 0

        if group is None and MODE_GROUP_ENVKEY in self.envConfigDict:
            group = self.envConfigDict[MODE_GROUP_ENVKEY]

        if timeout_s == -1:
            if MODE_TIMEOUT_DEFAULT_ENVKEY in self.envConfigDict:
                timeout_s = int(self.envConfigDict[MODE_TIMEOUT_DEFAULT_ENVKEY])
            else:
                timeout_s = TIMEOUT_DEFAULT_S

        isSet = self.mode.setPending(modeMember, timeout_s=timeout_s, group=group)        
        if isSet:
            try:
                return self.actionType(StandardAction.MODE_SET.value), modeMember, timeout_s
            except:
                return StandardAction.MODE_SET, modeMember, timeout_s

    async def activatePendingMode(self):
        """Activates the pending mode if set.
        
        As a result, the pending mode will be the active mode. The pending mode
        is cleared.
        
        :raise RuntimeError: If the control has not been set up for handling 
            modes.
        :raise ErrorMessage: `MODE_NOT_PENDING` – If no pending mode is set.
        
        :return: The activated mode.
        :rtype: Enum
        """

        if self.mode is None:
            raise RuntimeError("Control does not support modes.")

        try:
            mode = self.mode.activatePending()
            return self.modeType.fromAny(mode)
        except RuntimeError:
            raise ErrorMessage(
              "No pending mode is set.", 'MODE_NOT_PENDING', ()
            )

    async def unsetActiveMode(self):
        """Unsets the active mode if set.
        
        :return: The unset mode or `None` if no mode was unset.
        :rtype: Enum | None
        
        :raise RuntimeError: If the control has not been set up for handling 
            modes.
        """

        if self.mode is None:
            raise RuntimeError("Control does not support modes.")

        modeMember = self.modeType.fromAny(self.mode.getActive())
        if modeMember != StandardMode.NONE:
            self.mode.unsetActive()
            return modeMember
        return None

    async def setActiveMode(self, mode, group=None):
        """Sets the active mode.
        
        :param mode: The mode to set. Either a mode `Enum` value (`str`) or the
            mode `Enum` member itself.
            Passing the `NONE` mode `Enum` member is equivalent to calling
            :meth:`~.Control.unsetActiveMode`.
        :type mode: str | Enum
        :param group: The group ownership to be set on the mode directory. 
            Accepts a group name or numeric group ID (gid).
            If `None`, the :data:`~.MODE_GROUP_ENVKEY` environment value is
            used if it exists. If not, the group ownership is left unmodified.
        :type group: str | None
        
        :raises ValueError: If the mode `Enum` does not have a member
            corresponding to :paramref:`~.Control.setMode.mode`.
        :raise RuntimeError: If the control has not been set up for handling 
            modes.
        
        :return: A tuple with the following elements:
        
            * The actual action performed, as an action `Enum` member, i.e.
              `MODE_SET` or `MODE_UNSET`.
            * The mode `Enum` member that was set or unset, respectively.
            * `None` in the case of the `MODE_SET` action, ``0`` in the case
              of the `MODE_UNSET` action.
        :rtype: tuple
        """

        if self.mode is None:
            raise RuntimeError("Control does not support modes.")

        modeMember = self.modeType.fromAny(mode)
        
        if modeMember == StandardMode.NONE:
            modeMember = await self.unsetActiveMode()
            try:
                return self.actionType(StandardAction.MODE_UNSET.value), modeMember, 0
            except:
                return StandardAction.MODE_UNSET, modeMember, None

        if group is None and MODE_GROUP_ENVKEY in self.envConfigDict:
            group = self.envConfigDict[MODE_GROUP_ENVKEY]

        isSet = self.mode.setActive(modeMember, group=group)
        if isSet:
            try:
                return self.actionType(StandardAction.MODE_SET.value), modeMember, 0
            except:
                return StandardAction.MODE_SET, modeMember, None

    async def performAction(
        self,
        action,
        argument=None,
        isOkNodo=False,
        isForced=None,
        mayPrompt=None,
        correlationId=None
    ):
        """Performs an action.
        
        Custom actions are delegated to :meth:`~.Control._performAction`.
        
        :param action: The action to perform. Either an action `Enum` value
            (`str`) or an action `Enum` member itself. If the implementation of
            :meth:`~.Control._performAction` deems another action more
            appropriate, it must return it as **actual action**.
        :type action: str | Enum
        :param argument: The argument(s) required to perform the action. It may
            be a single object or an ordered sequence of several objects. It is
            up to the custom implementation of :meth:`~.Control._performAction`
            to evaluate the arguments properly – unless a default implementation 
            is used (see below). 
        :type argument: object | list  tuple
        :param isOkNodo: If `True`, a warning is suppressed if the action's
            target state is already reached.
        :type isOkNodo: bool
        :param isForced: If `True`, the action is forced even if the target
            state is already reached.
        :type isForced: bool
        :param mayPrompt: If `True`, the user may be prompted for any input,
            e.g. confirmations or passwords. `False` for 
            non-interactive operation.
        :type mayPrompt: bool
        :param correlationId: An ID for grouping messages in the message buffer.
            For instance, if some calls to :meth:`~.Control.performAction` use
            the correlation ID ``request29476``, and other calls use the ID 
            ``request39549``, the message buffer can specifically retrieve the
            corresponding messages instead of "all" messages.
        :type correlationId: str
        
        For the actions listed in :class:`~.StandardAction`, the default 
        implementation works as follows:
        
        * :attr:`~.StandardAction.MODE_SET`: Sets the pending mode by calling
          :meth:`~.Control.setPendingMode` unless
          :paramref:`~.Control.isSkipPendingMode` is `True`. In that case the
          active mode is set. Expects a sequence of
          :paramref:`~.Control.setPendingMode.mode` and 
          :paramref:`~.Control.setPendingMode.timeout_s` in
          :paramref:`~.Control.performAction.argument`.
        * :attr:`~.StandardAction.MODE_UNSET`: Unsets the pending mode by
          calling :meth:`~.Control.unsetPendingMode` unless
          :paramref:`~.Control.isSkipPendingMode` is `True`. In that case the
          active mode is unset. Expects `None` in
          :paramref:`~.Control.performAction.argument`.
        * :attr:`~.StandardAction.MODE_ACTIVATE`: Activates the pending mode by
          calling :meth:`~.Control.activatePendingMode`. Expects `None` in
          :paramref:`~.Control.performAction.argument`.
        
        :raise ValueError: If the action is not listed in the action `Enum`.
        :raise ValueError: If one of the parameters `isForced`, `mayPrompt` or
            `correlationId` is given, but :meth:`~.Control._performAction` does
            not declare it explicitly in its signature.
        """

        requestedAction = action
        action = self.actionType.fromAny(requestedAction)
        requestedArgument = copy.copy(argument)

        # Validate the requestedArgument(s) for standard actions.
        if action.value == StandardAction.MODE_SET.value:
            argument = [None, None]

            if isinstance(requestedArgument, tuple) or isinstance(requestedArgument, list):
                if len(requestedArgument) > 2:
                    raise ValueError("requestedArgument must be a tuple of mode and timeout in seconds.")
                argument[0] = requestedArgument[0]
                argument[1] = -1
                if len(requestedArgument) == 2 and isinstance(requestedArgument[1], int):
                    argument[1] = requestedArgument[1]
            else:
                argument[0] = requestedArgument

        if not action in StandardAction:
            # Prepare keyword arguments for _performAction().
            kwargs = { 
                'argument': argument,
                'isOkNodo': isOkNodo
            }

            supportedParameters = inspect.signature(self._performAction).parameters
            for kwarg in ('isForced', 'mayPrompt', 'correlationId'):
                try:
                    value = locals()[kwarg]
                except:
                    value = None
                if kwarg not in supportedParameters:
                    if value is not None:
                        raise ValueError("%s argument is not supported." % kwarg)
                    continue
                kwargs[kwarg] = value

        actualAction, actualArgument, feedback = (action, argument, None)
        error = None
        try:
            # Delegate standard actions to the corresponding methods.
            if action == StandardAction.MODE_UNSET:
                if self.isSkipPendingMode:
                    actualArgument = await self.unsetActiveMode()
                else:
                    actualArgument = await self.unsetPendingMode()
            elif action == StandardAction.MODE_SET:
                if self.isSkipPendingMode:
                    outcome = await self.setActiveMode(argument[0])
                else:
                    outcome = await self.setPendingMode(argument[0], timeout_s=argument[1])
                actualAction = outcome[0]
                actualArgument = (outcome[1], outcome[2])
                feedback = 'already' if actualAction == StandardAction.MODE_UNSET and actualArgument[0] is None else None
            elif action == StandardAction.MODE_ACTIVATE:
                actualArgument = await self.activatePendingMode()
            else:
                # Delegate all other actions to _performAction().
                outcome = await self._performAction(action, **kwargs)
                if outcome is None:
                    actualAction, actualArgument, feedback = (None, None, None)
                elif isinstance(outcome, tuple) or isinstance(outcome, list):
                    actualAction, actualArgument, feedback = outcome
                elif isinstance(outcome, Enum):
                    actualAction = outcome
                else:
                    actualArgument = outcome
        except SystemExit as exitPropagation:
            error = exitPropagation
        except Exception as rootError:
            error = rootError
        finally:
            if error is not None:
                if isinstance(error, CleaningUp) or isinstance(error, SystemExit):
                    raise error

                if not isinstance(error, ErrorMessage):
                    error = self._createExceptionFromActionEn(actualAction, actualArgument, error)
                if self.msgBuffer is not None:
                    self.msgBuffer.append(
                        error, ns=self.ns, correlationId=correlationId
                    )
            elif self.msgBuffer is not None and actualAction is not None:
                self._appendMessageFromActionEn(
                    actualAction, actualArgument, feedback,
                    isOkNodo=isOkNodo, correlationId=correlationId
                )

    @abstractmethod
    async def _performAction(
        self,
        requestedAction,
        argument=None,
        isOkNodo=False,
        **kwargs
    ) -> tuple:
        """Implements the logic for performing a custom action.
        
        :param kwargs: Any number of the following keyword arguments, depending
            on which the implementation wants to support:
            
            * :paramref:`~.Control.performAction.isForced`
            * :paramref:`~.Control.performAction.mayPrompt`
            * :paramref:`~.Control.performAction.correlationId`
        :type kwargs: dict
        
        :return: A tuple of three elements:
        
            * **actualAction** (`Enum`) – The actually performed action as an 
              action `Enum` member.
            * **actualArgument** (`object` | `list` | `tuple` | `None`) – The actually
              used argument(s) as a single object or a sequence of objects.
            * **feedback** (`str` | `None`) – A feedback identifier. See
              :ref:`feedback <control-feedback>`.
        
            If `None`, the control does not auto-create and append any messages.
        :rtype: tuple | None
        """

        raise NotImplementedError

    @abstractmethod
    def _invalidateCache(self):
        """Invalidates any cached property values.
        
        It is up to the custom implementation whether to cache property values.
        """

        raise NotImplementedError

    async def getProperty(self, property):
        """Retrieves the current value of a property.
        
        Custom properties are delegated to :meth:`~.Control._getProperty`.
        
        :param property: The property whose value to retrieve. Either a property
            `Enum` value (`str`) or the property `Enum` member itself.
        :type property: str | Enum
        
        For the properties listed in :class:`~.StandardProperty`, the default
        implementation works as follows:
        
        * :attr:`~.StandardProperty.MODE_PENDING`: Calls 
          :meth:`~.Control.getPendingMode`.
        * :attr:`~.StandardProperty.MODE_ACTIVE`: Calls 
          :meth:`~.Control.getActiveMode`.
        
        :raises ValueError: If the property `Enum` does not have a member
            corresponding to :paramref:`~.Control.getProperty.property`.
        
        :return: The current property value. It can be of any Python type, be it
           a scalar type like `int` or `str`, be it a `list` or `dict` or a
           custom object.
        :rtype: object
        """
        
        if self.propertyType is None:
            propertyMember = StandardProperty.fromAny(property)
        else:
            propertyMember = self.propertyType.fromAny(property)

        if propertyMember == StandardProperty.MODE_PENDING:
            return await self.getPendingMode()
        elif propertyMember == StandardProperty.MODE_ACTIVE:
            return await self.getActiveMode()

        return await self._getProperty(propertyMember)

    @abstractmethod
    async def _getProperty(self, property):
        """Implements the logic for retrieving the value of a custom property.
        
        :param property: The property whose value to retrieve. Either a property
            `Enum` value (`str`) or the property `Enum` member itself.
        :type property: str | Enum
        
        :return: The current property value. It may be a cached value if the
            custom implementation uses caching. To ensure fresh values, call
            :meth:`~.Control._invalidateCache` first.
        :rtype: object
        """

        raise NotImplementedError

    async def getPropertiesJson(self, propertiesListsEn=None):
        """Creates a JSON-formatted list of all current property values.
        
        :param propertiesListsEn: The property lists to be rendered in JSON 
            format, as returned by :meth:`~.Control._getPropertiesListsEn`.
            Only the first two elements of each property list are used.
        :type propertiesListsEn: tuple[str]
        
        :return: The properties list in JSON format.
        :rtype: str
        """

        propertiesListsEn = propertiesListsEn if propertiesListsEn else await self._getPropertiesListsEn()

        indentStr = MessageHelper.getSpaceIndent(self.jsonIndent)
        output = StringIO()
        output.write('{\n')

        for propertyListEn in propertiesListsEn:
            if isinstance(propertyListEn[0], Enum):
                name = propertyListEn[0].name
            else:
                name = str(propertyListEn[0])
            value = propertyListEn[1]

            output.write(indentStr)
            output.write('"')
            output.write(name)
            output.write('": ')
            output.write(MessageHelper.getJsonLiteral(value))
            if propertyListEn != propertiesListsEn[-1]:
                output.write(',')
            output.write('\n')
        output.write('}')
        return output.getvalue()

    async def getPropertiesTextEn(self, propertiesListsEn=None, firstColumnWidth=28):
        """Creates a human-readable listing of all current property values in
        English (`en`).
        
        The following is an example of such a listing::
        
            Mode pending:               none
            Mode active:                up
        
        The output contains two text columns for each property:
        
        #. The capitalized human-readable name of the property. If the property
           value matches :data:`~.TRUE_VALUE_PATTERN` or
           :data:`~.FALSE_VALUE_PATTERN`, a question mark is appended. For
           instance, ``is-powered-on`` becomes ``is powered on?``. If the
           property value has any other type, a colon is appended.
        #. The corresponding human-readable value.
        
        :param propertiesListsEn: The property lists to be rendered as plain text,
            as returned by :meth:`~.Control._getPropertiesListsEn`.
            Only the last two elements of each property list are used.
        :type propertiesListsEn: tuple[str]
        :param firstColumnWidth: The width of the first column in the log
            output, as number of characters.
        :type firstColumnWidth: int
        
        :return: The human-readable property listing.
        :rtype: str
        """

        propertiesListsEn = propertiesListsEn if propertiesListsEn else await self._getPropertiesListsEn()

        output = StringIO()
        for propertyListEn in propertiesListsEn:
            humanNameEn = propertyListEn[2]
            if not humanNameEn[0].isupper():
                humanNameEn = humanNameEn[0].upper() + humanNameEn[1:]

            humanValueEn = propertyListEn[3]
            if isinstance(humanValueEn, list) or isinstance(humanValueEn, tuple):
                if not len(humanValueEn):
                    humanValueEn = "none"
                else:
                    humanValueEn = ", ".join(humanValueEn)

            if humanValueEn is None:
                humanNameEn += ":"
            elif TRUE_VALUE_PATTERN.search(str(humanValueEn)) or FALSE_VALUE_PATTERN.search(str(humanValueEn)):
                humanNameEn += "?"
            else:
                humanNameEn += ":"

            output.write(('%%-%is%%s\n' % firstColumnWidth) % (humanNameEn, humanValueEn))

        return output.getvalue().strip()

    async def _getPropertiesListsEn(self):
        """Creates **property lists** for all properties and their current 
        values in English (`en`).
        
        The following is an example of a property list::
        
            ['POSITION', Position.UP, 'screen position', 'up']
        
        | ... where ``POSITION`` is the member name (`str`) of the property as 
          listed in the property `Enum`.
        | ... ``Position.UP`` is the current value of the property as returned
          by :meth:`~.Control.getProperty`.
        | ... ``screen position`` is the human-readable name (`str`) of the
          property.
        | ... and ``up`` is the current human-readable value of the property 
          (`str`).
        
        Each property list must contain these four elements in the order shown
        above.
        
        The default implementation works as follows:
        
        * The human-readable name is created from the property `Enum` member's
          value. Hyphens are replaced by spaces. As an example, ``mode-active`` 
          becomes ``mode active``.
        * As for the human-readable value, if the property value is an `Enum` 
          member, its value is used. If the property value is a `bool`, ``yes`` 
          or  ``no`` are produced. If it is `None`, ``n/a`` is produced.
          Otherwise, the unmodified property value is used.
        
        :return: An `Iterable` with one property list for each property.
        :rtype: Iterable
        """

        lists = []

        try:
            self._invalidateCache()
        except NotImplementedError:
            pass

        for propertyMember in StandardProperty if self.propertyType is None else self.propertyType:
            humanNameEn = propertyMember.value.replace('-', ' ')

            value = await self.getProperty(propertyMember)
            if isinstance(value, Enum):
                humanValueEn = value.value
            else:
                humanValueEn = value

            if humanValueEn is None:
                humanValueEn = "n/a"
            elif isinstance(humanValueEn, bool):
                humanValueEn = "yes" if humanValueEn else "no"

            lists.append([propertyMember.name, value, humanNameEn, humanValueEn])

        return lists

    def close(self):
        """Closes auto-created resources."""

        if self.isOwnMsgBuffer and self.msgBuffer is not None and hasattr(self.msgBuffer, 'close'):
            self.msgBuffer.close()


class ActionHelper:
    """Static functions for dealing with actions."""

    @staticmethod
    def getWordForms(action):
        """Creates a set of English word forms from a given action. The word
        forms are intended for use in human-readable message texts.
        
        Examples:
        
        * ``mode-set`` returns ``set``, ``setting``, ``set``, ``mode``, `None`
        * ``power-off`` or ``poweroff`` returns ``power``, ``powering``, ``powered``, `None`, ``off``
        * ``fade-out`` or ``fadeout`` returns ``fade``, ``fading``, ``faded``, `None`, ``out``
        * ``light-fade-out`` or ``light-fadeout`` returns ``fade``, ``fading``, ``faded``, ``light``, ``out``
        
        :param action: The action for which to create the word forms. Either a
            mode `Enum` value (`str`) or the action `Enum` member itself. 
        :type action: Enum | str
        
        :return: A tuple of five elements:
        
            * **action** (`str`) – The infinitive form of the action verb.
            * **presentParticiple** (`str`) – The present progressive form of 
              the action verb.
            * **pastParticiple** (`str`) – The past participle of the action
              verb.
            * **object** (`str` | `None`) – The object of the action, e.g. 
              ``mode`` or ``light``.
            * **addendum** (`str` | `None`) – A preposition or similar word
              form. That is, one of
              ``on``, ``off``, ``up``, ``right``, ``left``, ``down``, ``out``, 
              ``in``, ``min``, ``max``.
        :rtype: tuple
        """

        if action is None:
            return (None, None, None, None, None)

        if isinstance(action, Enum):
            action = action.value

        object = None
        addendum = None
        
        match = re.match(r'^(.*?)?-?(on|off|up|right|left|down|out|in|min|max)$', action)
        if match:
            action = match.group(1) if match.group(1) else 'switch'
            addendum = match.group(2)

        match = re.match(r'^(\w+)-(\w+)$', action)
        if match:
            object = match.group(1)
            action = match.group(2)
        
        if action == 'status':
            object = 'status'
            action = 'get'

        if action[-1] == 'e':
            presentParticiple = '%sing' % action[:-1]
            pastParticiple = '%sd' % action
        else:
            presentParticiple = '%sing' % action
            pastParticiple = '%sed' % action

        if action.endswith('ind'):
            pastParticiple = '%sound' % action.removesuffix('ind')
        elif action.endswith('break'):
            pastParticiple = '%sbroken' % action.removesuffix('break')
        elif action.endswith('catch'):
            pastParticiple = '%scaught' % action.removesuffix('catch')
        elif action.endswith('come'):
            pastParticiple = '%scame' % action.removesuffix('come')
        elif action.endswith('do'):
            pastParticiple = action + 'ne'
        elif action.endswith('eep'):
            pastParticiple = '%sept' % action.removesuffix('eep')
        elif action.endswith('ke'):
            if action == 'make':
                pastParticiple = 'made'
            else:
                pastParticiple = action + 'n'
        elif action.endswith('init'):
            presentParticiple = '%sializing' % action
            pastParticiple = '%sialized' % action
        elif action in ('hide', 'ride', 'forbid'):
            pastParticiple = '%sden' % action[:-1]
        elif action.endswith('hold'):
            pastParticiple = '%sheld' % action.removesuffix('hold')
        elif action == 'seek':
            pastParticiple = 'sought'
        elif action[-1] == 't' and not re.search(r'[aeiou]{2}n?t$', action):
            presentParticiple = '%sting' % action
            if action == 'burst':
                pastParticiple = 'burst'
            elif action == 'get':
                pastParticiple = 'got'
            elif re.search(r'[^e]et$', action) or action == 'put':
                pastParticiple = action
            elif action == 'shut':
                pastParticiple = 'shut'
        elif action.endswith('te'):
            pastParticiple = '%stten' % action.removesuffix('te')
        elif action[-1] == 'p':
            presentParticiple = '%sping' % action
            pastParticiple = '%sped' % action
        elif action.endswith('d'):
            if action.endswith('read'):
                pastParticiple = action

        return action, presentParticiple, pastParticiple, object, addendum


class ControlEnum(Enum):
    """Base class for `Enum` classes used by :class:`~.Control`.
    
    Note that a custom action, mode and property `Enum` does not have to extend
    this class, as the methods of this class are added dynamically at runtime.
    """

    @classmethod
    def fromAny(cls, object):
        """Looks for an `Enum` member corresponding to a given object.
        
        :param object: The `str` value of an `Enum` member or an `Enum` member
            itself.
        :type object: str | Enum
        
        :return: The corresponding `Enum` member.
        :rtype: Enum
        
        :raises ValueError: If the `Enum` does not have a corresponding member.
        """

        if isinstance(object, Enum):
            member = object
        else:
            try:
                member = cls(str(object))
            except:                
                raise ValueError("'%s' is unsupported." % str(object))
        return member

    def __eq__(self, other):
        """Makes an `Enum` member comparable with simple strings, based on its
        value.
        
        :param other: The `str` value of an `Enum` member or an `Enum` member
            itself.
        :type other: str | Enum
        """

        if type(self) == type(other):
            return self is other

        if isinstance(other, Enum):
            other = other.value

        return self.value == str(other)


class StandardAction(ControlEnum):
    """Standard actions supported by :meth:`~.Control.performAction` without the 
    need for custom implementations in :meth:`~.Control._performAction`.
    
    If a custom control wants to use the default implementation of 
    :meth:`~.Control.performAction` for a specific action, one of its action
    `Enum` members must have the same value as the desired standard action.
    
    Any action `Enum` passed into :paramref:`~.Control.actionType` will 
    dynamically inherit the methods of this class.
    """

    MODE_SET = 'mode-set'
    MODE_UNSET = 'mode-unset'
    MODE_ACTIVATE = 'mode-activate'


class StandardMode(ControlEnum):
    """Standard modes supported by the `Mode` methods of :class:`~.Control`.
    
    Any mode `Enum` passed into :paramref:`~.Control.modeType` will dynamically 
    inherit the methods of this class.
    """

    NONE = 'none'

    @classmethod
    def fromAny(cls, mode):
        """Extends :meth:`~.ControlEnum.fromAny` by treating `str`, ``none``
        (`str`) and `None` as equivalent.
        """

        if mode in ('', 'none', None):
            return StandardMode.NONE

        return ControlEnum.fromAny.__func__(cls, mode)

    def __eq__(self, other):
        """Extends :meth:`~.ControlEnum.__eq__` by treating `str`, ``none``
        (`str`) and `None` as equivalent.
        """

        isEqual = self.value in ('', 'none', None) and other in ('', 'none', None)
        if isEqual:
            return True

        return super(ControlEnum).__eq__(other)


class StandardProperty(ControlEnum):
    """Standard properties supported by :meth:`~.Control.getProperty` without 
    the need for custom implementations in :meth:`~.Control._getProperty`.
    
    If a custom control wants to use the default implementation of 
    :meth:`~.Control.getProperty` for a specific property, one of its property
    `Enum` members must have the same value as the desired standard property.
    
    Any property `Enum` passed into :paramref:`~.Control.propertyType` will
    dynamically inherit the methods of this class.
    """

    MODE_PENDING = 'mode-pending'
    MODE_ACTIVE = 'mode-active'
