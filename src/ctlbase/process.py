# SPDX-FileCopyrightText: 2026 54350963+xoocoon@users.noreply.github.com
#
# SPDX-License-Identifier: GPL-3.0-only

# This executable is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3.

# This executable is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this executable. If not, see <https://www.gnu.org/licenses/>.

"""Classes for handling OS processes and background tasks.

They integrate low-level Python constructs like :external:class:`subprocess.Popen`
and :external:func:`asyncio.create_subprocess_exec` into the `ctlbase` package.
"""

import sys
import os
from shutil import which

import asyncio
import aiofiles
import concurrent.futures
import inspect

from enum import Enum
import time
from datetime import datetime
import subprocess

import json
import re

from ctlbase.config import Caller, FileHelper
from ctlbase.message import Severity, LoggingMessageBuffer


TASK_CHECKING_INTERVAL_MS_DEFAULT = 1000
"""Default interval in milliseconds in which tasks are checked for their state."""

ANSI_ESCAPE_PATTERN = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')


# Exception to break time.sleep instances in UNIX signal handlers
class CleaningUp(Exception):
    pass


class SyncAdapter():
    """Provides a function to execute a coroutine or a normal function
    synchronously.
    """

    def __init__(self, loop=None):
        self.loop = loop

    async def syncify(self, func, /, *args, **keywords):
        if inspect.iscoroutinefunction(func):
            if self.loop:
                if self.loop.is_running():
                    return await self.loop.create_task(func(*args, **keywords))
                else:
                    return self.loop.run_until_complete(func(*args, **keywords))
            else:
                return await func(*args, **keywords)
        # Executes the synchronous function, e.g. from the pigpio module.
        return func(*args, **keywords)


class TaskMonitor():
    """A monitor for asyncio tasks.
    
    It awaits tasks running in the background, avoiding warnings like "Task was
    destroyed but it is pending!" or "coroutine was never awaited". What is
    more, results and error messages from completed tasks can be appended to a
    message buffer.
    
    Tasks to be monitored are added via :meth:`~.TaskMonitor.add`. They are
    regularly checked for success or failure.
    """

    def __init__(
        self,
        loop,
        taskCheckingInterval_ms=TASK_CHECKING_INTERVAL_MS_DEFAULT,
        msgBuffer=None,
        ns=True
    ):
        """Creates a task monitor.
        
        :param loop: See :attr:`~.TaskMonitor.loop`.
        :type loop: :external:class:`asyncio.EventLoop`
        :param taskCheckingInterval_ms: See 
            :attr:`~.TaskMonitor.taskCheckingInterval_ms`.
        :type taskCheckingInterval_ms: int
        :param msgBuffer: The message buffer to which results and error messages
            of completed tasks are appended. If `None`, results and errors are
            silently ignored.
        :type msgBuffer: MessageBuffer | None
        :param ns: The message namespace for appended messages. If `True`, the
            namespace is derived from the caller name. See
            :class:`~.CallerInspector` for the discovery algorithm. Only
            relevant if :paramref:`~.TaskMonitor.msgBuffer` is not `None`.
        :type ns: str
        """

        self.loop: asyncio.EventLoop = loop
        """The asyncio event loop in which new tasks are created, including the
        monitoring thread itself.
        """

        self.taskCheckingInterval_ms: int = taskCheckingInterval_ms
        """The interval in milliseconds in which tasks are checked for their
        state."""

        self.ns = Caller.getName().upper() if isinstance(ns, bool) and ns else ns
        self.msgBuffer = msgBuffer
        if self.msgBuffer is not None and self.msgBuffer.nsDefault is None:
            self.msgBuffer.nsDefault = self.ns

        self.tasks = set()
        self.errorCount = 0
        self.isStopping = False
        self.monitorTask = None

    def cancelTasksInLoop(self):
        """Cancels all tasks in the event loop and waits for them to complete.
        
        This applies to all the tasks in the event loop, no matter if they were
        added to the task monitor ot not.
        """

        for task in asyncio.all_tasks(loop=self.loop):
            if not task.done():
                task.cancel()

            try:
                self.loop.run_until_complete(task)
            except asyncio.exceptions.CancelledError:
                # Ignore re-raised cancellation errors.
                pass

    async def cancelTasks(self):
        """Cancels all tasks in the task monitor and waits for them to
        complete.
        """

        for task in self.tasks.copy():
            if not task:
                continue

            if not task.done():
                task.cancel()

            try:
                await task
            except asyncio.exceptions.CancelledError:
                # Ignore re-raised cancellation errors.
                pass

    async def stop(self):
        """Stops the task monitor and cancels all its tasks."""

        if not self.isStopping:
            self.isStopping = True

        if self.monitorTask:
            await self.monitorTask

        await self.cancelTasks()

    def add(self, task, name=None):
        """Adds a task to the task monitor.
        
        :param task: The task to be added. If it is a coroutine, it is scheduled
            as a task in the event loop.
        :type task: coroutine | :external:class:`asyncio.Task`
        
        :raise asyncio.InvalidStateError: If the event loop is already closed.
        
        :return: The added task. Only relevant if 
            :paramref:`~.TaskMonitor.add.task` is a coroutine.
        :rtype: :external:class:`asyncio.Task`
        """

        if self.loop.is_closed():
            raise asyncio.InvalidStateError("Error while adding task: loop is already closed.")

        if inspect.iscoroutine(task):
            task = self.loop.create_task(task, name=name)

        self.tasks.add(task)
        return task

    def remove(self, task):
        """Removes a task from the task monitor without cancelling it.
        
        :param task: The task to be removed.
        :type task: :external:class:`asyncio.Task`
        """

        self.tasks.remove(task)

    def start(self):
        """Creates and starts a monitoring thread in the event loop.
        
        Tasks can be added at any time, that is, before and after the monitor is 
        running.
        
        Added tasks are checked every
        :attr:`~.TaskMonitor.taskCheckingInterval_ms` milliseconds for their 
        state. If a task completed successfully, its result is appended to the
        message buffer with key `TASK_SUCCEEDED` unless it is `None`.
        If a task failed, the error message of its exception is appended to the
        message buffer with key `TASK_FAILED`.
        In any case, a completed task is removed from the task monitor.
        """

        self.isStopping = False
        self.errorCount = 0
        self.monitorTask = self.loop.create_task(asyncio.to_thread(self._monitorTasks), name='taskMonitor')

    def _monitorTasks(self):
        while not self.isStopping:
            for task in self.tasks.copy():
                if not task.done():
                    continue

                taskName = task.get_name() if hasattr(task, 'get_name') else None
                taskNameStr = ' ' + taskName if taskName else ''

                error = task.exception()
                if error and isinstance(error, CleaningUp):
                    os._exit(255)
                elif error and not isinstance(error, asyncio.exceptions.CancelledError):
                    self.errorCount += 1
                    self.remove(task)

                    if self.msgBuffer is not None:
                        self.msgBuffer.append(
                            "Error in task%s: %s (%s)" % (taskNameStr, str(error), type(error)),
                            'TASK_FAILED', (taskName, error),
                            Severity.ERROR, ns=self.ns
                        )
                else:
                    result = task.result()
                    if result is not None and self.msgBuffer is not None:
                        self.msgBuffer.append(
                            "Task%s completed with result: %s" % (str(result),),
                            'TASK_SUCCEEDED', (taskName, result),
                            Severity.ERROR, ns=self.ns
                        )
                    self.remove(task)

            time.sleep(float(self.taskCheckingInterval_ms) / 1000)


class CommandExecutor():
    """An executor for commands, i.e. executables with optional arguments. Each
    command is executed in a subprocess.
    
    Supported variants:
    
    * synchronous or asynchronous execution
    * a single command or multiple commands
    * as an executable with distinct arguments or as a shell command
    
    While the class follows an asyncio design, commands may or may not be
    executed in an asyncio event loop.
    """

    class SynchronousError(Exception):
        """An error that occurred while synchronously executing a command."""

        def __init__(self, returncode=None, message=None):
            super().__init__(message)

            self.returncode: int = returncode
            """The numeric return code of the command."""

            self.message: str = message
            """The message text of the error returned by the command."""

            self.lng: str | None = None
            """A two-letter language code for the language of the message text."""

    def __init__(
        self,
        msgBuffer=None,
        ns=True,               # 'ns' is i18next wording; True for auto-discovery.
        isSynchronousDefault=False
    ):
        """Creates a new command executor.
 
        :param msgBuffer: The message buffer to which messages are logged.
            Logging is always asynchronous. The actual log output can be 
            controlled per execution via the ``isLogEnabled`` parameter of the
            execution method.
        :type msgBuffer: LoggingMessageBuffer
        :param ns: The message namespace for logged messages. If `True`, the
            namespace is derived from the caller name. See
            :class:`~.CallerInspector` for the discovery algorithm.
        :type ns: str
        :param isSynchronousDefault: See :attr:`~.isSynchronousDefault`.
        :type isSynchronousDefault: bool
        """

        self.ns = Caller.getName().upper() if isinstance(ns, bool) and ns else ns
        self.msgBuffer = msgBuffer
        if self.msgBuffer is not None and self.msgBuffer.nsDefault is None:
            self.msgBuffer.nsDefault = self.ns

        self.isSynchronousDefault: bool = isSynchronousDefault
        """If `True` commands are executed synchronously by default."""

        self.executablePaths = {}

    def close(self):
        pass

    async def _createProcess(
        self,
        command,
        env=None,
        isStdIn=False,
        isStdOut=True,
        isStdErr=True,
        useAsyncio=False
    ):
        """Creates a subprocess for executing a command.
        
        The command can be given in one of two forms:
        
        #. As a `list` or `tuple` with one or more `str` instances. The first
           one denotes the executable, possibly with a path specification.
           All remaining `str` instances denote the arguments to be passed to
           the executable.
        #. As a single `str` denoting a shell command.
        
        If form 1 is used without a path specification, the path to the
        executable is resolved via :meth:`~.FileHelper.findBinary` and
        :external:func:`shutil.which`, then cached to speed up subsequent calls.
        
        :param command: The command to execute, depending on the chosen form
            (see above).
        :type command: list[str] | tuple[str] | str
        :param env: Environment variables to be passed to the subprocess, as
            defined for :external:class:`subprocess.Popen`.
        :type env: dict
        :param isStdIn: If `True`, the subprocess is prepared for receiving
            data from the parent process via *stdin*.
        :type isStdIn: bool
        :param isStdOut: If `True`, the parent process is prepared for
            receiving data from the subprocess via *stdout*.
        :type isStdOut: bool
        :param isStdErr: If `True`, the parent process is prepared for
            receiving data from the subprocess via *stderr*.
        :type isStdErr: bool
        :param useAsyncio: If `True`, the subprocess is created with asyncio.
            Consequently, the communication with the subprocess takes place in
            an asyncio event loop.
        
        :raise FileNotFoundError: If the executable could not be resolved to a
            path.
        
        :return: The created subprocess. Its type depends on the value of
            :paramref:`~.useAsyncio`.
        :rtype: :external:class:`subprocess.Popen` | :external:class:`asyncio.subprocess.Process`
        """

        if type(command) not in (list, tuple, str):
            raise ValueError('command must be a list of strings (command with distinct arguments) or a single string (shell command).')

        stdin = asyncio.subprocess.PIPE if isStdIn else None
        stdout = asyncio.subprocess.PIPE if isStdOut else None
        stderr = asyncio.subprocess.PIPE if isStdErr else None

        # Handle form 1.
        if isinstance(command, list) or isinstance(command, tuple):
            if not '/' in command[0]:
                # Resolve command to absolute path, possibly cached.
                if command[0] in self.executablePaths:
                    executablePath = self.executablePaths[command[0]]
                else:
                    try:
                        executablePath = FileHelper.findBinary(command[0])
                        self.executablePaths[command[0]] = executablePath
                    except FileNotFoundError:
                        executablePath = which(command[0])
                        self.executablePaths[command[0]] = executablePath

                if executablePath is None:
                    raise FileNotFoundError("Executable %s could not be found on the system." % command[0])

                if isinstance(command, list):
                    command[0] = executablePath
                else:
                    command = (executablePath, *command[1:])

            if useAsyncio:
                return await asyncio.create_subprocess_exec(
                    command[0], *command[1:], stdin=stdin, stdout=stdout, stderr=stderr, env=env
                )
            else:
                return subprocess.Popen(
                    command, stdin=stdin, stdout=stdout, stderr=stderr, env=env
                )

        # Handle form 2.
        if useAsyncio:
            return await asyncio.create_subprocess_shell(
                command, stdin=stdin, stdout=stdout, stderr=stderr, env=env
            )
        else:
            return subprocess.Popen(
                command, shell=True, stdin=stdin, stdout=stdout, stderr=stderr, env=env
            )

    async def executeSynchronously(
        self,
        command,
        env=None,
        stdInput=None,
        timeout_ms=None,
        outputEncoding='utf-8',
        isLogEnabled=True
    ):
        """Executes a single command in a subprocess and waits for its
        completion in an asyncio event loop.
        
        :param command: The command to execute in one of the forms defined for
            :meth:`~._createProcess`.
        :type command: list[str] | tuple[str] | str
        :param env: Environment variables to be passed to the subprocess, as
            defined for :external:class:`subprocess.Popen`.
        :type env: dict
        :param stdInput: Data to be sent to the subprocess via *stdin*. If
            `None`, no data is sent.
        :type stdInput: bytes | bytearray | str | None
        :param timeout_ms: Timeout in milliseconds to wait for the subprocess to
            complete. If the timeout expires, the subprocess is killed. If
            `None`, the  subprocess might run indefinitely.
        :type timeout_ms: int | None
        :param outputEncoding: An encoding specifier like ``utf-8`` for decoding
            the bytes from *stdout* and *stderr* into a `str`. If `None`, bytes
            are returned unmodified.
        :type outputEncoding: str | None
        :param isLogEnabled: If `True`, all occurring messages are written to
            the command executor's message buffer. For more fine-grained
            behavior, use the values :attr:`~.CommandExecutor.ON_SUCCESS_ONLY`
            and :attr:`~.CommandExecutor.ON_ERROR_ONLY`.
        :type isLogEnabled: bool | str
        
        :raise TimeoutError: If :paramref:`~.executeSynchronously.timeout_ms` is
            not `None` and the timeout is reached before the subprocess
            completes.
        :raise SynchronousError: If an error occurs in the subprocess.
        :raise FileNotFoundError: If the executable could not be resolved to a
            path.
        
        :return: Data received from the subprocess via *stdout*, possibly
            decoded to a `str`. See
            :paramref:`~.CommandExecutor.executeSynchronously.outputEncoding`
            for decoding options.
        :rtype: bytes | str
        """

        stdoutBytes = None
        stderrBytes = None
        returncode = 0
        flatCommand = None

        try:
            # Change codepage to UTF-8 if running on Windows.
            if os.name == 'nt':
                process = await self._createProcess('chcp 65001 >NUL', env=env)
                await process.communicate()

            if not env:
                env = os.environ.copy()
            env['COLOR_PRINT'] = 'none'

            if stdInput is not None:
                if isinstance(stdInput, str):
                    stdInput = stdInput.encode('utf-8')
                elif not isinstance(stdInput, bytearray) and not isinstance(stdInput, bytes):
                    raise ValueError("stdInput must be of type str, bytearray or bytes.")

            if self.msgBuffer is not None and str(isLogEnabled) != CommandExecutor.ON_ERROR_ONLY:
                flatCommand = ' '.join(command) if type(command) in (list, tuple,) else command

            # Execute the command in a subprocess.
            process = await self._createProcess(
                command, env=env, isStdIn=bool(stdInput is not None), useAsyncio=True
            )
            stdoutBytes, stderrBytes = await asyncio.wait_for(
                process.communicate(input=stdInput),
                float(timeout_ms) / 1000 if timeout_ms else None
            )
            returncode = process.returncode
        except (subprocess.TimeoutExpired, asyncio.exceptions.TimeoutError):
            return None

        # Convert returned bytes to a str if an encoding is given.
        if isinstance(stdoutBytes, bytes) and outputEncoding:
            stdoutBytes = stdoutBytes.decode(outputEncoding, errors='replace')
            stdoutBytes = stdoutBytes.strip()
        if isinstance(stderrBytes, bytes) and outputEncoding:
            stderrBytes = stderrBytes.decode(outputEncoding, errors='replace')
            stderrBytes = stderrBytes.strip()

        if returncode == 0:
            if self.msgBuffer is not None and isLogEnabled and \
            str(isLogEnabled) != CommandExecutor.ON_ERROR_ONLY:
                self.msgBuffer.append(
                    "Executed command: %s." % flatCommand, 'COMMAND_EXECUTED',
                    (flatCommand,), Severity.VERBOSE, ns=self.ns
                )
        else:
            if self.msgBuffer is not None and isLogEnabled and \
            str(isLogEnabled) != CommandExecutor.ON_SUCCESS_ONLY:
                errorMsgBytes = stderrBytes if stderrBytes else stdoutBytes

                msg = "Exit code %i executing command: %s" % (returncode, flatCommand)
                if isinstance(errorMsgBytes, str) and errorMsgBytes:
                    msg += ": %s" % errorMsgBytes

                self.msgBuffer.append(
                    msg, 'COMMAND_FAILED',
                    (returncode, flatCommand, errorMsgBytes),
                    Severity.VERBOSE, ns=self.ns
                )
            raise CommandExecutor.SynchronousError(
                returncode, stderrBytes if stderrBytes else stdoutBytes
            )

        return stdoutBytes

    async def executeAsynchronously(
        self,
        command,
        env=None,
        isLogEnabled=True
    ):
        """Executes a single command in a subprocess without waiting for
        completion.
        
        :param command: The command to execute in one of the forms defined for
            :meth:`~._createProcess`.
        :type command: list[str] | tuple[str] | str
        :param env: Environment variables to be passed to the subprocess, as
            defined for :external:class:`subprocess.Popen`.
        :type env: dict
        :param isLogEnabled: If `True`, all occurring messages are written to
            the command executor's message buffer. For more fine-grained
            behavior, use the values :attr:`~.CommandExecutor.ON_SUCCESS_ONLY`
            and :attr:`~.CommandExecutor.ON_ERROR_ONLY`.
        :type isLogEnabled: bool | str
        
        :raise FileNotFoundError: If the executable could not be resolved to a
            path.
        """

        if self.msgBuffer is not None and str(isLogEnabled) != CommandExecutor.ON_ERROR_ONLY:
            flatCommand = ' '.join(command)
            self.msgBuffer.append(
                "Starting command: %s" % flatCommand, 'COMMAND_STARTING',
                (flatCommand,), Severity.VERBOSE, ns=self.ns
            )

        await self._createProcess(command, env=env, useAsyncio=False)

    async def executeCommands(
        self,
        commands,
        stdInput=None,
        isSynchronous=None,
        timeout_ms=None,
        outputEncoding='utf-8',
        isLogEnabled=True
    ):
        """Executes one or more commands with the given options.
        
        A single command can be given in one of the forms defined for
        :meth:`~._createProcess`.
 
        What is more, if command form 1 is used, multiple commands can be
        wrapped in another `list` or `tuple`.
        
        :param commands: The command(s) to execute.
        :type commands: list[list] | tuple[tuple] | list[str] | tuple[str] | str
        :param stdInput: Data to be sent to the subprocess via *stdin*. If
            `None`, no data is sent via *stdin*. Must be `None` if
            :paramref:`~.executeCommands.isSynchronous` evaluates to `True`.
        :type stdInput: bytes | bytearray | str | None
        :param isSynchronous: If `True`, all commands are executed
            synchronously. If `False`, all commands are executed asynchronously.
            If `None`, the value of :attr:`~.isSynchronousDefault` is used.
        :type isSynchronous: bool | None
        :param timeout_ms: Timeout in milliseconds as described for
            :paramref:`~.executeSynchronously.timeout_ms`. Must be `None`
            if :paramref:`~.executeCommands.isSynchronous` evaluates to `True`.
        :type timeout_ms: int | None
        :param outputEncoding: An encoding specifier as described for
            :paramref:`~.executeSynchronously.outputEncoding`. Must be `None` if
            :paramref:`~.executeCommands.isSynchronous` evaluates to `True`.
        :type outputEncoding: str | None
        :param isLogEnabled: If `True`, all occurring messages are written to
            the command executor's message buffer. For more fine-grained
            behavior, use the values :attr:`~.CommandExecutor.ON_SUCCESS_ONLY`
            and :attr:`~.CommandExecutor.ON_ERROR_ONLY`.
        :type isLogEnabled: bool | str
        
        :raise ValueError: If :paramref:`~.isSynchronous` evaluates to `False`
            and at least one synchronous-only parameter has a value other than
            `None`.
        :raise FileNotFoundError: If the executable could not be resolved to a
            path.
        
        :return: | A `list` of `bytes` if :paramref:`~.isSynchronous` evaluates
              to `True` and :paramref:`~.outputEncoding` is `None`. Each entry
              represents the data received from one subprocess via *stdout*. 
              Index ``0`` corresponds to the first executed command, i.e. index
              ``0`` in :paramref:`~.executeCommands.commands`. Index ``1``
              corresponds to the second command and so on.
            | If :paramref:`~.outputEncoding` is not `None` all *stdout* output
              is concatenated to one `str` with newlines.
            | `None` if :paramref:`~.executeCommands.isSynchronous` evaluates to
              `False`.
        :rtype: list[bytes] | str | None
        """

        if not commands:
            return None

        if type(commands) in (list, tuple):
            if len(commands) < 1:
                return None

            if type(commands[0]) not in (list, tuple, str):
                raise ValueError('commands must be a list of string lists (multiple commands with distinct arguments) or a list of strings (multiple shell commands).')

            if type(commands[0]) in (list, tuple):
                # list in a list => one or more executables with arguments
                pass
            elif type(commands[0]) == str:
                # list with strings => a single executable with arguments
                commands = [commands]
        elif type(commands) == str:
            # a single string => a single shell command
            commands = [commands]
        else:
            raise ValueError('commands must be a list of strings (a single command with distinct arguments) or a single string (shell command).')

        env = os.environ.copy()
        stdoutBytes = []

        if isSynchronous or (isSynchronous is None and self.isSynchronousDefault):
            for command in commands:
                result = await self.executeSynchronously(
                    command, env=env, stdInput=stdInput, timeout_ms=timeout_ms,
                    outputEncoding=outputEncoding, isLogEnabled=isLogEnabled
                )
                stdoutBytes.append(result)
        else:
            if stdInput is not None:
                raise ValueError("stdInput may only be used with synchronous command execution.")

            for command in commands:
                await self.executeAsynchronously(
                    command, env=env, isLogEnabled=isLogEnabled
                )

            return

        if outputEncoding:
            return "\n".join(stdoutBytes) if len(stdoutBytes) else None
        else:
            return stdoutBytes

    async def getOutput(self, command):
        """Executes a single command synchronously and returns the output of
        *stdout* or *stderr* as an UTF-8 `str`.
        
        Hence, it is a simplified version of :meth:`~.executeSynchronously` for
        retrieving the output of an command. No logging is done.
        
        Possibly existing ANSI control characters are removed from the output,
        so it can be easily processed programmatically.
        
        :raise FileNotFoundError: If the executable could not be resolved to a
            path.
        
        :return: The output of *stdout* if the exit code of the subprocess is
            ``0``. The output of *stderr* otherwise.
        :rtype: str
        """

        try:
            output = await self.executeSynchronously(
                command, outputEncoding='utf-8', isLogEnabled=False
            )
        except CommandExecutor.SynchronousError as error:
            output = error.message

        # Remove ANSI control characters.
        ANSI_ESCAPE_PATTERN.sub('', output)

        if not output:
            raise RuntimeError('Could not retrieve output from %s.' % str(command))

        return output

    ON_SUCCESS_ONLY = 'ON_SUCCESS_ONLY'
    """If passed to :paramref:`~.executeCommands.isLogEnabled`, only success
    messages are logged.
    """

    ON_ERROR_ONLY = 'ON_ERROR_ONLY'
    """If passed to :paramref:`~.executeCommands.isLogEnabled`, only error
    messages are logged.
    """
