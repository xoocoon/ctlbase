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

"""Base classes for implementing CLI utilities and daemons.

:class:`~.ControlShell` and :class:`~.DaemonControlShell` serve as Python
context managers for common resources such as message buffers and process
executors. They also handle command line arguments and output to *stdout*,
*stderr* and/or log files.
"""

import sys
import os
import locale

import asyncio
import signal
import inspect

import copy
import re
import argparse

from ctlbase.config import Caller, BashHelper
from ctlbase.message import Severity, ErrorMessage, MessageHelper, LoggingMessageBuffer
from ctlbase.translation import Translation
from ctlbase.process import CleaningUp, TaskMonitor, CommandExecutor
from ctlbase.control import Control

try:
    from systemd import daemon # yes
except:
    # Sending systemd *ready* notifications will not be supported.
    pass


class ControlShell():
    """A context manager for creating CLI utilities. Creates and lifecyle-manages
    the resources typically needed by a control.
    """

    DEFAULT_KEY_PATTERN = re.compile(r'\[\w+\]')

    def __init__(
        self,
        parser,
        commandExecutor=None,
        envConfigName=True,
        msgBuffer=None,
        lng=None
    ):
        """Creates a new control shell with the resources specified in the 
        parameters. Independently of the parameters, always creates a new
        asyncio event loop for the current thread.
        
        :param parser: The parser or already parsed namespace with the command
            line arguments to evaluate.
        :type parser: :external:class:`argparse.ArgumentParser` | :external:class:`argparse.Namespace`
        :param commandExecutor: A command executor or `True` for auto-creation.
            `None` if no command executor is needed.
        :type commandExecutor: :class:`~.CommandExecutor` | True | None
        
        :param envConfigName: An environment configuration in one of the
            following forms:
            
            * As a configuration file name. It may may include a relative or
              absolute path. See :meth:`~.FileHelper.find` for the discovery
              algorithm.
            * As `True` to auto-discover a configuration file based on the 
              caller's normalized module name as returned by 
              :meth:`~.Caller.getName`. See 
              :meth:`~.FileHelper.findConfig` for the discovery algorithm.
            * As `None` if the control shell does not need an environment 
              configuration.
        :type envConfigName: tuple[str, dict] | str | bool | None
        :param msgBuffer: The message buffer to which all occurring messages are
            appended. `True` for auto-creation. If `None`, no messages are
            printed to *stdout* and *stderr*.
        :type msgBuffer: :class:`~.MessageBuffer` | True | None
        :param lng: A two-letter language code for the messages to be printed to
            *stdout* and *stderr*. If `None`, the defaults of the 
            :mod:`~.translation` module are in effect.
        :type lng: str | True
        """

        if commandExecutor is not None and not type(commandExecutor) in (CommandExecutor, bool):
            raise ValueError("commandExecutor must be of type CommandExecutor or True for auto-creation.")

        self.parser = parser        
        self.actionDest = None
        
        self.commandExecutor = commandExecutor
        """The process executor lifecycle-managed by this context manager."""

        self.envConfigName = envConfigName
        """The absolute path of the environment configuration file parsed into
        :attr:`~.ControlShell.envConfigDict`."""
        
        self.envConfigDict = None
        """The `dict` holding the parsed environment configuration."""

        self.msgBuffer = msgBuffer
        """The message buffer used by this context manager to print output to
        *stdout* and *stderr*.
        """

        if lng is not None:
            Translation.changeLanguage(lng)

        self.isOwnLoop = None
        self.loop = None
        self.isOwnThreadExecutor = None
        self.isOwnCommandExecutor = None
        self.control = None
        self.isOwnMsgBuffer = None
        self.exitCode = None
        self.textOnExit = None
        self.isClosed = False

    def __enter__(self):
        if self.envConfigName is None:
            self.envConfigName, self.envConfigDict = (None, None,)
        elif (isinstance(self.envConfigName, bool) and self.envConfigName) or isinstance(self.envConfigName, str):
            try:
                self.envConfigName, self.envConfigDict = BashHelper.parseConfig(self.envConfigName)
            except FileNotFoundError as error:
                self.envConfigName, self.envConfigDict = (None, None,)
                raise error
        else:
            raise ValueError("envConfigName must represent a path as str or True for auto-discovery.")

        if isinstance(self.parser, argparse.Namespace):
            self.args = ControlShell.interpolateConfigValues(self.parser, self.envConfigDict)
        else:
            knownArgs, unknownArgs = self.parser.parse_known_args()
            # Have the main parser reparse the arguments unknown to the subparser.
            # This allows standard arguments like --noprompt to be appended to a subparser's arguments.
            if unknownArgs and len(unknownArgs):
                knownArgs = self.parser.parse_args(args=unknownArgs, namespace=knownArgs)
            self.args = ControlShell.interpolateConfigValues(knownArgs, self.envConfigDict)

        if hasattr(self.args, 'action') and not self.args.action:
            print("An action must be provided. See --help for more information.", file=sys.stderr)
            sys.exit(1)

        for signalId in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP,):
            signal.signal(signalId, self.requestCleanup)

        self.isOwnLoop = True
        self.loop = asyncio.new_event_loop()
        
        if isinstance(self.msgBuffer, bool) and self.msgBuffer is not None:
            self.isOwnMsgBuffer = True
            ns = Caller.getName().upper()
            self.msgBuffer = LoggingMessageBuffer(self.loop, ns)
        else:
            self.isOwnMsgBuffer = False
            if self.msgBuffer is None:
                self.msgBuffer = None

        if isinstance(self.commandExecutor, bool) and self.commandExecutor:
            self.isOwnCommandExecutor = True
            self.commandExecutor = CommandExecutor(
                msgBuffer=self.msgBuffer, isSynchronousDefault=True
            )

        return self

    def __exit__(self, errorType, error, traceback):
        """When the context manager exits, the following steps are taken:
    
        * If the context manager exits due to an exception of type 
          :class:`~.ErrorMessage` or `ValueError`, the corresponding error 
          message is added to the message buffer.
        * Any other exception bubbles up, i.e. is not handled gracefully.
        * An auto-created process executor is auto-closed.
        * The `close` function or coroutine function of the control set via 
          :meth:`~.ControlShell.setControl` is called if it exists.
        * The `close` function or coroutine function of this context manager is
          called if it exists. Useful for extensions of :class:`~.ControlShell`
          that control resources of their own.
        * Messages in the message buffer are printed to *stdout* and *stderr* as
          follows:
          
          * If the ``-j`` | ``--json`` command line flag is given, all messages
            in the message buffer are printed to *stdout* in JSON format. All 
            remaining flags for output handling are ignored at this point.
            If the ``-j`` | ``--json`` command line flag is not given, plain 
            text messages are printed to *stdout* unless the severity is
            :attr:`~.Severity.WARNING` or :attr:`~.Severity.ERROR`. Then they
            are printed to *stderr*.
          * The language of plain text messages is determined by 
            :paramref:`~.ControlShell.lng`.
          * If the ``-v`` | ``--verbose`` command line flag is given, messages 
            with severity :attr:`~.Severity.VERBOSE` and :attr:`~.Severity.DEBUG`
            are included in plain text output.
          * Plain text messages are colored according to their severities, 
            unless :attr:`~.MessageBuffer.isColoringDefault` is `False`.
            Coloring is also suppressed if the ``COLOR_PRINT`` environment 
            variable is ``false``.
          * Any plain text string set in :meth:`~.ControlShell.printOnExit` is 
            printed to *stdout*.
        * An auto-created message buffer is auto-closed.
        * The context manager's asyncio event loop is closed.
        
        If an exit code was set with :meth:`~.ControlShell.setExitCode`, it is
        used as the exit code of the Python process. Otherwise the exit code is
        determined via the message buffer's :meth:`~.MessageBuffer.getExitCode`
        method.
        """

        isExceptionHandled = False

        try:
            if self.isOwnCommandExecutor and self.commandExecutor:
                self.commandExecutor.close()

            if isinstance(error, CleaningUp) or isinstance(error, SystemExit):
                # CleaningUp causes the __exit__ method to be entered, so simply ignore the exception.
                isExceptionHandled = True
            elif isinstance(error, ErrorMessage):
                if self.msgBuffer is not None:
                    self.msgBuffer.append(error)
                isExceptionHandled = True
            elif isinstance(error, ValueError):
                if self.msgBuffer is not None:
                    self.msgBuffer.append(str(error), 'ARGUMENT_INVALID', (error,), Severity.WARNING)
                isExceptionHandled = True
            elif isinstance(error, Exception):
                raise error

            if error is not None and self.msgBuffer is None:
                print(error, file=sys.stderr)
                sys.exit(1)

            if (self.msgBuffer is None or not self.msgBuffer) and self.textOnExit is None:
                sys.exit(0)

            if self.msgBuffer is not None:
                if Translation.language != 'en':
                    self.msgBuffer.addTranslations()

                if hasattr(self.args, 'json') and self.args.json or (hasattr(self.args, 'json_fragment') and self.args.json_fragment):
                    if hasattr(self.args, 'json_fragment') and self.args.json_fragment:
                        print(self.msgBuffer.getJson().strip('[]\n'))
                    elif self.msgBuffer or self.textOnExit:
                        print(MessageHelper.packMessageAndStatusJson(self.msgBuffer.getJson(), self.textOnExit))
                else:
                    if not hasattr(self.msgBuffer, 'isLogToConsole') or not self.msgBuffer.isLogToConsole:
                        if hasattr(self.args, 'verbose') and self.args.verbose:
                            self.msgBuffer.printText(
                                severityFilter=Severity, 
                                lngForText=Translation.language
                            )
                        else:
                            self.msgBuffer.printText(
                                lngForText=Translation.language
                            )

                    if self.textOnExit is not None:
                        print(self.textOnExit)
            elif self.textOnExit is not None:
                print(self.textOnExit)
            
            if self.exitCode is not None:
                sys.exit(self.exitCode)
            else:
                sys.exit(self.msgBuffer.getExitCode())
        finally:
            # Close the attached control (if any).
            if self.control is not None:
                if isinstance(self.control, Control) and hasattr(self.control, 'close'):
                    try:
                        if inspect.iscoroutinefunction(self.control.close):
                            self.loop.run_until_complete(self.control.close())
                        else:
                            self.control.close()
                    except:
                        # Ignore exception as we are closing anyway.
                        pass

            if self.isOwnMsgBuffer and self.msgBuffer is not None and hasattr(self.msgBuffer, 'close'):
                self.msgBuffer.close()

            # Close this shell instance (if it has extra resources that need to be freed.)
            if not self.isClosed and hasattr(self, 'close'):
                try:
                    if inspect.iscoroutinefunction(self.control.close):
                        self.isClosed = self.loop.run_until_complete(self.close())
                    else:
                        self.close()
                except:
                    # Ignore exception as we are closing anyway.
                    pass

            if self.isOwnLoop and self.loop:
                self.loop.close()

        return isExceptionHandled

    def requestCleanup(self, signalNumber=None, stackFrame=None):
        """A Python signal handler injecting a :class:`~.CleaningUp` exception
        into all running code in the current Python process.
        
        The signature is defined in :external:func:`signal.signal`.
        """

        raise CleaningUp

    def setControl(self, control: Control):
        """Associates the given control with this context manager.
        
        The effects are as follows:
        
        * The context manager's message buffer is propagated to the control.
          If the context manager does not have a message buffer, the opposite
          takes place: The control's message buffer is propagated to the context
          manager.
        * If the context manager's message buffer does not yet have a  default
          namespace, the namespace of the control is set as its default 
          namespace.
        * When the context manager exits, the `close` function or coroutine
          function of the control is called if one exists.
        """

        self.control = control

        # msgBuffer of the shell takes precedence.
        if self.msgBuffer is not None:
            self.control.msgBuffer = self.msgBuffer
        # If the shell does not have a msgBuffer, take the one from the control.
        elif self.control.msgBuffer is not None:
            self.msgBuffer = self.control.msgBuffer

        if self.msgBuffer is not None and self.msgBuffer.nsDefault is None:
            self.msgBuffer.nsDefault = self.control.ns
        
        if self.commandExecutor:
            self.commandExecutor.msgBuffer = self.msgBuffer

    def setExitCode(self, exitCode):
        """Sets a numeric code to use as exit code when this context manager
        exits.
        
        :param exitCode: The exit code to set.
        :type exitCode: int
        
        :raise ValueError: If the exit code is not between 0 and 255.
        """
        if exitCode < 0 or exitCode > 255:
            raise ValueError("exitCode must be between 0 and 255.")

        self.exitCode = exitCode
        
    def printOnExit(self, text):
        """Sets an additional text to be printed to *stdout* when this context
        manager exits.
        
        If a text is set already, the given text is appended with a newline
        character.
        
        :param text: The verbatim text to print to *stdout*.
        :type text: str
        """

        if self.textOnExit is None:
            self.textOnExit = text
        else:
            self.textOnExit += '\n' + text

    @staticmethod
    def addStandardArgs(parser, isAddAction=True):
        """Amends an argument parser with arguments common to CLI utilities.
        
        To create a control shell that handles these arguments automatically,
        simply pass the amended parser to :meth:`~.ControlShell.__init__`.
        
        It is recommended to add custom arguments before amending the parser
        with standard arguments.
        
        * ``-j`` | ``--json`` flag: Print JSON output to *stdout*. Handled by 
          :meth:`~.ControlShell.__exit__` 
        * ``-v`` | ``--verbose`` flag: Print more verbose messages to *stdout*.
          Handled by :meth:`~.ControlShell.__exit__`.
        * ``-o`` | ``--oknodo`` flag: Suppress an error if the requested
          action's target state is already reached. Passed to 
          :meth:`~.Control.performAction` as :paramref:`~.Control.performAction.isOkNodo` .
        * ``--force`` flag: Force the action even if the target state is already
          reached. Passed to :meth:`~.Control.performAction` as 
          :paramref:`~.Control.performAction.isForced`.
        * ``--noprompt`` flag: Do not prompt the user for any input, e.g. 
          confirmations or passwords. Its inversion is passed to
          :meth:`~.Control.performAction` as :paramref:`~.Control.performAction.mayPrompt`.
        * *action* (positional argument): The action to perform. An *action*
          argument is only added if not yet present in the parser and if
          :paramref:`~.ControlShell.addStandardArgs.isAddAction` is set to
          `True`.
        
        :param parser: The argument parser to amend.
        :type parser: :external:class:`argparse.ArgumentParser`
        :param isAddAction: If `False`, no *action* positional argument is 
            added.
        :type isAddAction: bool
        """

        parser.add_argument('-j', '--json', action='store_true', help="Print JSON output to stdout instead of plain text.")
        parser.add_argument('-v', '--verbose', action='store_true', help="Print more verbose messages to stdout (if available).")
        parser.add_argument('-o', '--oknodo', action='store_true', help="Suppress an error if the requested action's target state is already reached. The exit code will be 0 instead of 3.")
        parser.add_argument('--force', action='store_true', help="Force the action even if the target state is already reached.", default=None)
        parser.add_argument('--noprompt', action='store_true', help="Do not prompt the user for any input, e.g. confirmations or passwords.", default=None)

        if isAddAction and not any(a.dest == 'action' for a in parser._actions):
            parser.add_argument('action', help="The action(s) to perform.")

    @staticmethod
    def interpolateConfigValue(value, envConfigDict):
        if isinstance(value, str) and ControlShell.DEFAULT_KEY_PATTERN.match(value):
            configKey = value.strip('[]')            
            if envConfigDict and configKey in envConfigDict:
                return envConfigDict[configKey]
            else:
                return None
        return value
        
    @staticmethod
    def interpolateConfigValues(args, envConfigDict):
        if isinstance(args, argparse.Namespace):
            argDict = vars(args)
        elif isinstance(args, dict):
            argDict = args

        for key, value in argDict.items():
            if isinstance(value, list) or isinstance(value, tuple):
                valuesIn = copy.copy(value)
                valuesOut = []
                for value in valuesIn:
                    valuesOut.append(ControlShell.interpolateConfigValue(value, envConfigDict))
                argDict[key] = valuesOut
            else:
                argDict[key] = ControlShell.interpolateConfigValue(value, envConfigDict)

        return args
    
    def standardToOperateArgs(self):
        standardToOperateArgName = {
            'oknodo': 'isOkNodo',
            'force': 'isForced',
            'noprompt': 'mayPrompt'
        }
        
        kwargs = {}
        
        argDict = vars(self.args)
        for argName in standardToOperateArgName:
            if not argName in argDict:
                continue
            
            if argName == 'noprompt' and argDict[argName] is not None:
                kwargs[standardToOperateArgName[argName]] = not argDict[argName]
            else:
                kwargs[standardToOperateArgName[argName]] = argDict[argName]
        
        return kwargs


class DaemonControlShell(ControlShell):
    """A context manager for creating daemons, i.e. long-running processes that
    monitor system resources and / or provide a service endpoint.
    
    It adds the following features to :class:`~.ControlShell`:
    
    * Auto-creation and lifecycle management for a task monitor. It can be used
      to schedule tasks in the background, i.e. in the context manager's asyncio
      event loop.
    * Support for command line arguments common to daemons.
    """

    def __init__(
        self,
        parser,
        taskMonitor=None,
        commandExecutor=None,
        envConfigName=None,
        msgBuffer=None,
        lng=None,
        **kwargs
    ):
        """
        Arguments with the same name have the same meaning as with 
        :meth:`~.ControlShell.__init__`.
        
        :param taskMonitor: A task monitor or `True` for auto-creation. `None`
            if no task monitor is needed.
        :type taskMonitor: :class:`~.TaskMonitor` | True | None
        """

        super().__init__(
            parser,
            commandExecutor=commandExecutor,
            envConfigName=envConfigName,
            msgBuffer=msgBuffer,
            lng=lng
        )

        if taskMonitor is not None and not type(taskMonitor) in (TaskMonitor, bool):
            raise ValueError("taskMonitor must be of type TaskMonitor or True for auto-creation.")

        self.taskMonitor = taskMonitor
        """The task manager lifecycle-managed by this context manager."""

        self.isOwnTaskMonitor = False

    def __enter__(self):
        super().__enter__()

        if not hasattr(self.args, 'log') or not self.args.log:
            raise ValueError("--log argument must be given.")

        if isinstance(self.taskMonitor, bool) and self.taskMonitor:
            self.isOwnTaskMonitor = True
            self.taskMonitor = TaskMonitor(self.loop, msgBuffer=self.msgBuffer)

        # Depending on arguments, add logging capability to msgBuffer.
        if self.msgBuffer is not None and self.args.log:
            if self.args.tswidth:
                self.loop.run_until_complete(
                    self.msgBuffer.openLog(logPath=self.args.log, firstColumnWidth=self.args.tswidth)
                )
            else:
                self.loop.run_until_complete(
                    self.msgBuffer.openLog(logPath=self.args.log)
                )

        if self.msgBuffer is not None and self.args.print:
            self.msgBuffer.isLogToConsole = True

        return self

    def __exit__(self, errorType, error, traceback):
        """When the context manager exits, the following steps are taken in 
        addition to :class:`~.ControlShell`:
        
        * An auto-created task monitor is auto-closed.
        """

        if self.msgBuffer is not None:
            self.msgBuffer.append("Terminating the process.", 'TERMINATING', (), Severity.NORMAL)

        # Close this shell instance (if it has extra resources that need to be freed.)
        if not self.isClosed and hasattr(self, 'close'):
            try:
                self.isClosed = self.loop.run_until_complete(self.close())
            except:
                # Ignore exception as we are closing anyway.
                pass

        if self.isOwnTaskMonitor and self.taskMonitor:
            self.loop.run_until_complete(self.taskMonitor.stop())

        return super().__exit__(errorType, error, traceback)

    async def notifyReady(self):
        """Sends a systemd *ready* notification.
        
        Useful if the daemon is wrapped into a systemd service unit.
        """

        if hasattr(self.args, 'delay'):
            await asyncio.sleep(float(self.args.delay) / 1000)

        try:
            daemon.notify('READY=1')
        except NameError:
            raise RuntimeError("systemd-python is not available. Have installed the 'systemd' dependency?")
        except:
            # Obviously, this process does not run in a systemd context.
            pass

    @staticmethod
    def addStandardArgs(parser):
        """Amends an argument parser with arguments common to daemons.
        
        To create a control shell that handles these arguments automatically,
        simply pass the amended parser to :meth:`~.DaemonControlShell.__init__`.
        
        It is recommended to add custom arguments before amending the parser
        with standard arguments.
        
        * ``--delay``: Delay in milliseconds after which the systemd is notified
          *ready*. Handled by :meth:`~.DaemonControlShell.notifyReady`.
        * ``-e`` | ``--errors``: Maximum number of errors after which the daemon
          exits. To be handled by subclasses of :class:`~.DaemonControlShell`.
        * ``-l`` | ``--log``: Path to the daemon's log file. Handled by
          :meth:`~.DaemonControlShell.__enter__`.
        * ``--tswidth``: The width of the first column in the log output, as
          number of characters. Handled by :meth:`~.DaemonControlShell.__enter__`.
        * ``--print`` flag: Print messages to *stdout* in addition to the log
          file. Handled by :meth:`~.DaemonControlShell.__enter__`.
        
        :param parser: The argument parser to amend.
        :type parser: :external:class:`argparse.ArgumentParser`
        """

        parser.add_argument('--delay', type=int, help='Delay in milliseconds after which systemd is notified ready.', default=1000)
        parser.add_argument('-e', '--errors', type=int, help='Maximum number of errors after which the daemon shall exits (with code 4).', default=64)
        parser.add_argument('-l', '--log', type=str, help="Path to the daemon's log file.")
        parser.add_argument('--tswidth', type=int, help='The width of the first column in the log output, as number of characters.', default=32)
        parser.add_argument('--print', action='store_true', help="Print messages to stdout in addition to the log file.")

