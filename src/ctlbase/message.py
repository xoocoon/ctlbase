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

"""Classes for capturing messages from controls and other sources like REST APIs.

Messages can be appended to an instance of :class:`~.MessageBuffer` before
actually printing them to the console and/or a log file. An individual message
is captured with multiple attributes, not just with its human-readable text.
This has two benefits:

* Messages can be translated into another language in one go and on-demand. See
  :meth:`~.MessageBuffer.addTranslations`.
* Message semantics can be queried programatically, e.g. by checking for a
  certain message type via :meth:`~.MessageBuffer.hasKey`.  Another example is
  checking for a certain message severity like :attr:`~.Severity.ERROR` via
  :meth:`~.MessageBuffer.hasSeverity`.
"""

import sys
import os

import aiofiles

import copy
from enum import Enum
from numbers import Number
import time
from datetime import datetime
from io import StringIO
import json
import re

from ctlbase.config import TRUE_VALUE_PATTERN, FALSE_VALUE_PATTERN, FileHelper
from ctlbase.translation import Translation


COLOR_PRINT_ENVKEY = 'COLOR_PRINT'
"""Environment configuration key for the coloring of text printed to *stdout*
or *stderr*."""


class Severity(Enum):
    """The severity of a single message.

    The member values denote the ANSI color codes for corresponding messages
    printed to the console and/or a log file.
    """

    NORMAL = 39
    """Informative message with no special coloring."""

    VERBOSE = 37
    """Verbose message providing more detail than :attr:`~.Severity.NORMAL`."""

    DEBUG = 90
    """Technical message intended for debugging."""

    SUCCESS = 92
    """Success message, e.g. for a completed action."""

    WARNING = 93
    """Warning message, e.g. for an action that completed, yet in an unexpected
    way.
    """

    ERROR = 91
    """Error message, e.g. for a failed action."""


SEVERITY_FILTER_DEFAULT = (
    Severity.NORMAL, Severity.SUCCESS,
    Severity.WARNING, Severity.ERROR
)
"""Default filter for message severities when iterating over messages. The value
is in effect for the entire Python process."""


class Message():
    """A message captured with several attributes."""

    MESSAGE_COUNT_PATTERN = re.compile(r'"KEY"')

    def __init__(
        self,
        textEn,
        key,
        arguments=(),
        severity: Severity=Severity.NORMAL,
        timestamp_iso=None,
        ns=None,
        correlationId=None
    ):
        """Creates a message.
        
        :param textEn: See :attr:`~.Message.textEn`.
        :type textEn: str
        :param key: See :attr:`~.Message.key`.
        :type key: str
        :param arguments: See :attr:`~.Message.arguments`.
        :type arguments: list | tuple | dict
        :param severity: See :attr:`~.Message.severity`.
        :type severity: Severity
        :param timestamp_iso: See :attr:`~.Message.timestamp_iso`.
        :type timestamp_iso: str | datetime.datetime | None
        :param ns: See :attr:`~.Message.ns`.
        :type ns: str
        :param correlationId: See :attr:`~.Message.correlationId`.
        :type correlationId: str | None
        """

        self.textEn: str = textEn
        """The human-readable message text in English (`en`). It serves
        multiple purposes:

        * At runtime: If the message is to be shown to a user whose preferred
          language is `en`, the message text is used "as is".
        * If the user has a different preferred language, the English message
          text is used as a fallback if no appropriate translation is available.
        * At development time: If the English message text occurs as a literal
          `str` in the source code, it serves as an additional documentation of
          the source code, e.g. a literal error message for a specific case.
        * Also, it serves as the primary reference when translating the message
          text into other languages.
        """

        self.key: str = key
        """| An alphanumeric key for the message semantics.
          It is used to check for messages that have occurred, as well as a key
          into translation files.
          It must be unique within the message namespace.
        | Example: ``FILE_NOT_FOUND``
        """

        self.arguments: list | tuple | dict = arguments
        """The message arguments, i.e. language-neutral literals that can be
        embedded in the message texts of various languages.
        Examples:

        * an `int` like ``5`` for the number of failed retries
        * a `str` representing a user-supplied value, e.g. ``/tmp/command.log``
          for a log file path
        * a `float` like ``1.532`` representing the elapsed time in seconds

        If the arguments are specified as a `dict` instead of an ordered `list`
        or `tuple`, the `dict` keys must be semantic identifiers for the
        arguments. Examples:

        * ``retryCount`` for the number of failed retries
        * ``log`` for a log file path
        * ``elapsed`` for the elapsed time
        """

        self.severity: Severity = severity
        """The message severity."""

        self.timestamp_iso: str | datetime.datetime | None = timestamp_iso
        """| An optional timestamp in ISO 8601 format or an instance of
          :external:class:`datetime.datetime` directly. It should represent
          the point in time when the message occurred originally.
        | Example: ``2024-08-01T14:38:32.499588``
        """

        self.ns: str = ns
        """| An alphanumeric identifier for the message namespace within which all
          message keys must be unique. It usually represents the application or
          domain from which the message originates.
        | If `None`, when appending the message to a message buffer, the default
          namespace of the message buffer is assumed. See 
          :attr:`~.MessageBuffer.nsDefault`.
        | Examples: ``HOME_THEATER`` or ``ENGINE_CONTROL``
        """

        self.correlationId: str | None = correlationId
        """An optional correlation ID, i.e. an alphanumeric identifier used to
        group several messages. As an example, it can be an identifier for a
        batch command that produces several messages across several message
        namespaces.
        """

    def __eq__(self, other):
        """Compares this message to another object by all attributes except
        the `str` values of the message texts.
        """

        try:
            if isinstance(other, str):
                other = Message.fromJson(other)
            elif isinstance(other, dict):
                other = Message.fromDict(other)
            elif not isinstance(other, Message):
                return False
        except:
            return False

        if self.ns != other.ns:
            return False
        if self.key != other.key:
            return False
        if self.severity != other.severity:
            return False
        if self.arguments != other.arguments:
            return False
        if self.correlationId != other.correlationId:
            return False

        if isinstance(self.timestamp_iso, str):
            selfTimestamp = datetime.fromisoformat(self.timestamp_iso)
        else:
            selfTimestamp = self.timestamp_iso
        if isinstance(other.timestamp_iso, str):
            otherTimestamp = datetime.fromisoformat(other.timestamp_iso)
        else:
            otherTimestamp = other.timestamp_iso
        if selfTimestamp != otherTimestamp:
            return False

        selfTextLngs = self.getTextLngs()
        otherTextLngs = other.getTextLngs()
        if selfTextLngs != otherTextLngs:
            return False

        return True

    @staticmethod
    def fromJson(jsonStr):
        """Creates a :class:`~.Message` instance from a JSON `str`.

        It may be overridden to deserialize a message from a custom JSON
        representation. In this case, :meth:`~.Message.toJson` and 
        :meth:`~.Message.fromDict` must be overridden, too.

        :param jsonStr: A message in its JSON representation, enclosed in
            ``{ ... }``.
        :type jsonStr: str

        :return: The created message.
        :rtype: Message
        """

        jsonStr = MessageHelper.unpackMessages(jsonStr)
        if not jsonStr.strip():
            count = 0
        else:
            match = Message.MESSAGE_COUNT_PATTERN.findall(jsonStr)
            count = len(match) if match and len(match) > 0 else 0

        if count != 1:
            raise ValueError("jsonStr must contain exactly one message object.")

        return Message.fromDict(json.loads(jsonStr))

    @staticmethod
    def fromDict(msgDict):
        """Creates a :class:`~.Message` instance from a `dict`.

        The `dict` must correspond to the JSON representation defined via
        :meth:`~.Message.fromJson` and :meth:`~.Message.toJson`. Hence, if
        these two methods are overridden, :meth:`~.Message.fromDict` must be
        overridden, too.

        :param msgDict: A message in its `dict` representation.
        :type msgDict: dict

        :return: The created message.
        :rtype: Message
        """

        if 'TEXT_EN' not in msgDict:
            raise ValueError("msgDict must contain a 'TEXT_EN' key.")
        if 'KEY' not in msgDict:
            raise ValueError("msgDict must contain a 'KEY' key.")

        msgObject = Message(msgDict['TEXT_EN'], msgDict['KEY'])

        for key, value in msgDict.items():
            attrName = re.sub(r'_([a-z])', lambda m: m.group(1).capitalize(), key.lower())
            setattr(msgObject, attrName, value)

        if not isinstance(msgObject.severity, Severity):
            msgObject.severity = Severity[msgObject.severity]

        return msgObject

    def toJson(self):
        """Represents this message as a JSON `str`.

        It may be overridden to serialize the message to a custom JSON
        representation. In this case, :meth:`~.Message.fromJson` and 
        :meth:`~.Message.fromDict` must be overridden, too.

        The following is an example for the default JSON representation::

            {
                "NAMESPACE": "SCREEN",
                "KEY": "SET_MODE_SUCCEEDED",
                "ARGUMENTS": [
                    "PREVENT_DOWN",
                    30
                ],
                "SEVERITY": "SUCCESS",
                "TEXT_EN": "Set mode prevent-down for 30 seconds.",
                "TEXT_DE": "Modus prevent-down für 30 Sekunden gesetzt."
            }

        :return: The message in its JSON representation, enclosed in
            ``{ ... }``.
        :rtype: str
        """

        if self.timestamp_iso is None:
            timestamp_iso = datetime.now(tz=None).isoformat()
        elif isinstance(self.timestamp_iso, datetime):
            timestamp_iso = self.timestamp_iso.isoformat()
        elif not isinstance(self.timestamp_iso, str):
            raise ValueError("timestamp_iso must be a string in ISO datetime format or an instance of datetime.")

        output = StringIO()
        output.write('{\n')
        if self.correlationId:
            output.write('"CORRELATION_ID": "')
            output.write(self.correlationId)
            output.write('",\n')
        if self.ns:
            output.write('"NAMESPACE": "')
            output.write(self.ns)
            output.write('",\n')
        output.write('"KEY": "')
        output.write(self.key)
        output.write('",\n')
        output.write('"ARGUMENTS": ')
        if not self.arguments:
            output.write('[]')
        elif type(self.arguments) in (list, tuple, dict):
            if not any(bool(a) for a in self.arguments):
                output.write('[]')
            else:
                output.write(MessageHelper.getJsonLiteral(self.arguments))
        else:
            output.write(MessageHelper.getJsonLiteral((self.arguments,)))
        output.write(',\n')
        output.write('"SEVERITY": "')
        output.write(self.severity.name)
        output.write('",\n')
        if self.timestamp_iso:
            output.write('"TIMESTAMP_ISO": "')
            output.write(self.timestamp_iso)
            output.write('",\n')

        textLngs = self.getTextLngs()
        for i in range(0, len(textLngs)):
            textLng = textLngs[i]

            output.write('"TEXT_')
            output.write(textLng.upper())
            output.write('": "')
            output.write(MessageHelper.escape(getattr(self, 'text%s' % textLng.capitalize()).strip()))
            if i < len(textLngs) - 1:
                output.write('",\n')
            else:
                output.write('"\n')

        output.write('}')
        return output.getvalue()

    def getTextLngs(self):
        """Determines the languages for which message texts are already rendered
        and available.

        Message texts for additional languages can be added by calling
        :meth:`~.MessageBuffer.addTranslations`. For that purpose, the message
        must be appended to a message buffer.

        :return: A sequence of two-letter language codes for the available
            message texts.
        :rtype: list[str]
        """

        lngs = []
        for textAttribute in self.__dict__.keys():
            if not textAttribute.startswith('text'):
                continue
            lngs.append(textAttribute[-2:].lower())
        return lngs

    def toText(self, lng='en'):
        """Looks up the message text in a certain language. It must already be
        available as a message attribute, as this method does not perform
        any on-demand translation.

        :param lng: Two-letter language code for the message text to be
            returned.
        :type lng: str

        :return: The message text for language `lng`, if available. Otherwise,
            the English message text is returned as a fallback.
        :rtype: str
        """

        try:
            text = getattr(self, 'text%s' % lng.capitalize())
        except:
            # Use 'en' as a fallback.
            text = self.textEn

        return text

    def toColoredText(self, lng='en'):
        """A variant of :meth:`~.Message.toText` that encloses the message text
        in ANSI control characters coloring the text according to severity.
        """

        return MessageHelper.getColoredText(self.toText(lng=lng), self.severity)


class ErrorMessage(Exception, Message):
    """A variant of :class:`~.Message` that works like an :external:class:`Exception`.

    It has an implicit severity of :attr:`~.Severity.ERROR` and can be "raised".

    Attributes with the same name have the same meaning as with :class:`~.Message`.
    """

    def __init__(
        self,
        textEn,
        key,
        arguments=(),
        timestamp_iso=None,
        ns=None,
        correlationId=None,
        exitCode=None
    ):
        """Creates an error message.
        
        :param textEn: See :attr:`~.Message.textEn`.
        :type textEn: str
        :param key: See :attr:`~.Message.key`.
        :type key: str
        :param arguments: See :attr:`~.Message.arguments`.
        :type arguments: list | tuple | dict
        :param severity: See :attr:`~.Message.severity`.
        :type severity: Severity
        :param timestamp_iso: See :attr:`~.Message.timestamp_iso`.
        :type timestamp_iso: str | datetime.datetime | None
        :param ns: See :attr:`~.Message.ns`.
        :type ns: str
        :param correlationId: See :attr:`~.Message.correlationId`.
        :type correlationId: str | None
        :param exitCode: See :attr:`~.ErrorMessage.exitCode`.
        :type exitCode: int | None
        """
        
        super().__init__(textEn)
        Message.__init__(
            self, textEn, key, arguments, Severity.ERROR, timestamp_iso, ns, correlationId
        )

        self.exitCode: int | None = exitCode
        """An optional numeric code to be used as exit code of the process. See
        :meth:`~.MessageBuffer.getExitCode`.
        """


class MessageBuffer():
    """A buffer for messages.

    Messages can be appended without writing them to an output stream directly.
    Appended messages can be queried programatically until the buffer is cleared
    via :meth:`~.MessageBuffer.clear`. For instance, to check for a certain
    message severity like :attr:`~.Severity.ERROR`, use the
    :meth:`~.MessageBuffer.hasSeverity` method.

    To eventually print the messages to *stdout*, *stderr* or another output 
    stream, use the :meth:`~.MessageBuffer.printText` method.

    The ``COLOR_PRINT`` environment variable determines the default behavior for
    coloring in text output functions if :attr:`~.MessageBuffer.isColoringDefault`
    was initialized as `None`. The allowed values for the environment variable
    are ``true`` and ``false``.
    """

    def __init__(
        self,
        nsDefault,
        lngDefault='en',
        isColoringDefault=None
    ):
        """Creates a new message buffer.
        
        :param nsDefault: See :attr:`~.MessageBuffer.nsDefault`.
        :type nsDefault: str
        :param lngDefault: See :attr:`~.MessageBuffer.lngDefault`.
        :type lngDefault: str
        :param isColoringDefault: If `True`, text is colored by default when 
            printed to *stdout* or *stederr*. If `None`, a possibly existing 
            :data:`~.COLOR_PRINT_ENVKEY` environment value is used. If the 
            latter does not exist, a default value of `True` is assumed.
        :type isColoringDefault: bool | None
        """

        self.nsDefault: str = nsDefault
        """The default namespace to be assigned to a message if its namespace
        is `None`.
        """

        self.lngDefault: str = lngDefault
        """Two-letter language code to be used as default when printing text to
        *stdout* or *stderr*.
        """

        self.isColoringDefault = isColoringDefault
        """If `True`, colored text is colored by default when printed to 
        *stdout* or *stderr*.
        """

        if isColoringDefault is None and 'COLOR_PRINT' in os.environ:
            if TRUE_VALUE_PATTERN.search(os.environ['COLOR_PRINT']):
                self.isColoringDefault = True
            elif FALSE_VALUE_PATTERN.search(os.environ['COLOR_PRINT']):
                self.isColoringDefault = False
        elif isColoringDefault is None:
            self.isColoringDefault = True

        self.clear()

    def __iter__(self):
       """Starts a new iteration over all the messages in the buffer."""

       return MessageIterator(self)

    def __len__(self):
        return len(self.msgObjects)

    def __invalidateCache(self):
        self.jsonStrCached = None

    def clear(self):
        """Clears all messages in the buffer.

        The default values of the buffer are retained.
        """

        self.msgObjects = []
        self.lastErrorExitCode = None
        self.__invalidateCache()

    def append(
        self,
        msg,
        key=None,
        arguments=(),
        severity: Severity=Severity.NORMAL,
        timestamp_iso=None,
        ns=None,
        correlationId=None,
        isDeduplicate=True
    ):
        """Appends a message to the buffer.
        The message may be given in one of two forms:

        #. As an instance of :class:`~.Message` or :class:`~.ErrorMessage`,
           provided in :paramref:`~.MessageBuffer.append.msg`. Example::

            msg = Message("Configuration file not found", 'CONFIG_NOT_FOUND', severity=Severity.ERROR)
            msgBuffer.append(msg)
        #. As individual attributes, of which the English message text must be
           provided in :paramref:`~.MessageBuffer.append.msg`. 
           What is more, :paramref:`~.MessageBuffer.append.key` must be 
           provided. All other attributes are optional and assume default values
           if not provided. Example::

            msgBuffer.append("Configuration file not found", 'CONFIG_NOT_FOUND', severity=Severity.ERROR)
        
        Regardless of the chosen form, if the message namespace is `None`, the
        value of :attr:`~.MessageBuffer.nsDefault` is used.
        
        :param msg: The message to append, depending on the chosen form
            (see above).
        :type msg: Message | ErrorMessage | str
        :param key: Only relevant if form 2 is chosen. See 
            :attr:`~.Message.key`.
        :type key: str
        :param arguments: Only relevant if form 2 is chosen. See 
            :attr:`~.Message.arguments`.
        :type arguments: list | tuple | dict
        :param severity: Only relevant if form 2 is chosen. See 
            :attr:`~.Message.severity`.
        :type severity: Severity
        :param timestamp_iso: Only relevant if form 2 is chosen. See 
            :attr:`~.Message.timestamp_iso`.
        :type timestamp_iso: str
        :param ns: Only relevant if form 2 is chosen. See :attr:`~.Message.ns`.
        :type ns: str
        :param correlationId: Only relevant if form 2 is chosen. See 
            :attr:`~.Message.correlationId`.
        :type correlationId: str | None
        :param isDeduplicate: If `True`, appends the message only if it is not
            yet contained in the buffer, according to :meth:`~.Message.__eq__`.
        :type isDeduplicate: bool
        """

        if isinstance(msg, str):
            msg = Message(
                msg, key, arguments, severity, timestamp_iso, ns, correlationId
            )
        else:
            if isinstance(msg, ErrorMessage):
                if msg.exitCode is not None:
                    self.lastErrorExitCode = msg.exitCode
            elif not isinstance(msg, Message):
                raise ValueError("msg must be of type Message, str or ErrorMessage")

            # If a Message instance is given, allow overriding individual
            # attributes.
            if not msg.textEn:
                raise ValueError("textEn must contain a message in English.")
            if not msg.key:
                if key:
                    msg.key = key
                else:
                    raise ValueError("key must be given.")
            if not msg.severity and severity:
                msg.severity = severity
            if not msg.timestamp_iso and timestamp_iso:
                msg.timestamp_iso = timestamp_iso
            if not msg.ns and ns:
                msg.ns = ns
            if not msg.correlationId and correlationId:
                msg.correlationId = correlationId

        if msg.ns is None:
            msg.ns = self.nsDefault

        if isDeduplicate and msg in self.msgObjects:
            return

        self.msgObjects.append(msg)
        self.__invalidateCache()

    def appendJson(self, jsonStr, correlationIdFilter=None):
        """Parses the messages in a JSON `str` and appends them to the buffer.
        
        Helpful if messages from another control or REST service were retrieved
        as JSON and need to be appended to the buffer.
        
        The supported JSON representation is defined by
        :meth:`~.Message.fromJson`. Multiple messages must be enclosed in
        ``[ ... ]``.
        
        :param jsonStr: The source JSON `str` from which to append messages.
        :type jsonStr: str
        :param correlationIdFilter: An optional filter for messages by
            correlation ID. See :paramref:`~.MessageIterator.correlationIdFilter`.
        :type correlationIdFilter: str | None
        """

        jsonStr = MessageHelper.unpackMessages(jsonStr)
        if not jsonStr.strip():
            count = 0
        else:
            match = Message.MESSAGE_COUNT_PATTERN.findall(jsonStr)
            count = len(match) if match and len(match) > 0 else 0

        if not count:
            return

        msgObjects = json.loads(jsonStr)
        if isinstance(msgObjects, dict):
            # jsonStr represents a single message.
            msgObjects = (msgObjects,)

        for msgObject in MessageIterator(
            msgObjects, correlationIdFilter=correlationIdFilter
        ):
            self.append(msgObject)

    def appendBuffer(self, otherBuffer, correlationIdFilter=None):
        """Appends the messages from another buffer to this buffer.

        :param otherBuffer: The source buffer from which to append messages.
        :type otherBuffer: MessageBuffer
        :param correlationIdFilter: An optional filter for messages by 
            correlation ID. See :paramref:`~.MessageIterator.correlationIdFilter`.
        :type correlationIdFilter: str | None
        """

        for msgObject in MessageIterator(
            otherBuffer, format=MessageIterator.Format.OBJECT,
            isImmutable=True, correlationIdFilter=correlationIdFilter
        ):
            self.append(msgObject)

    def printText(
        self,
        lngForText=None,
        severityFilter=SEVERITY_FILTER_DEFAULT,
        correlationIdFilter=None,
        isColoring=None,
        stdStream=sys.stdout,
        errStream=sys.stderr
    ):
        """Writes the messages in the buffer to an output stream as plain text.
        The messages are separated by newline characters.
        
        :param lngForText: Two-letter language code for the message texts to be
            printed. If `None`, the value of :attr:`~.MessageBuffer.lngDefault`
            is used. Defaults to ``en`` if a corresponding translation cannot be
            found.
        :type lngForText: str
        :param severityFilter: An optional filter for messages by severity.
            See :paramref:`~.MessageIterator.severityFilter`.
        :type severityFilter: Iterable | None
        :param correlationIdFilter: An optional filter for messages by 
            correlation ID. See :paramref:`~.MessageIterator.correlationIdFilter`.
        :type correlationIdFilter: str | None
        :param isColoring: If `True`, the text is colored according to message
            severity. If `None`, the value of
            :attr:`~.MessageBuffer.isColoringDefault` is used.
        :type isColoring: bool | None
        :param stdStream: The output stream to which all messages are printed by
            default. The only exceptions are messages with severities
            :attr:`~.Severity.WARNING` and :attr:`~.Severity.ERROR`. These are 
            printed to the :paramref:`~.MessageBuffer.printText.errStream`, if 
            it is not `None`.
        :type stdStream: io.TextIOBase
        :param errStream: An optional output stream to which messages with 
            severities :attr:`~.Severity.WARNING` and :attr:`~.Severity.ERROR`
            are printed.
        :type errStream: io.TextIOBase | None
        """

        lngForText = self.lngDefault if lngForText is None else lngForText
        isColoring = self.isColoringDefault if isColoring is None else isColoring

        for msgObject in MessageIterator(
            self, format=MessageIterator.Format.OBJECT,
            severityFilter=severityFilter,
            correlationIdFilter=correlationIdFilter,
            lngForText=lngForText
        ):
            try:
                text = getattr(msgObject, 'text%s' % lngForText.capitalize())
            except:
                # Use 'en' as a fallback.
                text = msgObject.textEn

            if isColoring:
                text = msgObject.toColoredText(lngForText)

            text = text.replace('\\n', '\n')

            if errStream is not None and msgObject.severity in (Severity.WARNING, Severity.ERROR):
                errStream.write(text)
                errStream.write('\n')
            else:
                stdStream.write(text)
                stdStream.write('\n')

    def getList(self, correlationIdFilter=None):
        """Creates an ordered sequence of message objects in this buffer.

        :param correlationIdFilter: An optional filter for messages by 
            correlation ID. See :paramref:`~.MessageIterator.correlationIdFilter`.
        :type correlationIdFilter: str | None

        :return: An ordered sequence of message objects.
        :rtype: list[Message]
        """

        if not correlationIdFilter:
            return self.msgObjects

        msgObjects = []
        for msgObject in MessageIterator(
            self, format=MessageIterator.Format.OBJECT,
            isImmutable=True, correlationIdFilter=correlationIdFilter
        ):
            msgObjects.append(msgObject)

        return msgObjects

    def getJson(self, correlationIdFilter=None):
        """Creates a formatted JSON `str` from the messages in the buffer.

        The supported JSON representation is defined by :meth:`~.Message.toJson`.

        :param correlationIdFilter: An optional filter for messages by 
            correlation ID. See :paramref:`~.MessageIterator.correlationIdFilter`.
        :type correlationIdFilter: str | None

        :return: The messages in their JSON representation, as an ordered list
            enclosed in ``[ ... ]``.
        :rtype: str
        """

        if self.jsonStrCached is not None:
            return self.jsonStrCached

        output = StringIO()
        output.write(MessageHelper.getJsonLiteral(self.getList(correlationIdFilter=correlationIdFilter)))
        self.jsonStrCached = output.getvalue()

        return self.jsonStrCached

    def getText(
        self,
        lngForText=None,
        severityFilter=SEVERITY_FILTER_DEFAULT,
        correlationIdFilter=None,
        isColoring=None
    ):
        """Concatenates the message texts in the buffer into one `str`.

        :param lngForText: Two-letter language code for the message texts to be
            concatenated. If `None`, the value of
            :attr:`~.MessageBuffer.lngDefault` is used. Defaults to ``en`` if a
            corresponding translation cannot be found.
        :type lngForText: str
        :param severityFilter: An optional filter for messages by severity.
            See :paramref:`~.MessageIterator.severityFilter`.
        :type severityFilter: Iterable | None
        :param correlationIdFilter: An optional filter for messages by 
            correlation ID. See :paramref:`~.MessageIterator.correlationIdFilter`.
        :type correlationIdFilter: str | None
        :param isColoring: If `True`, the returned text is colored according to
            message severity. If `None`, the value of
            :attr:`~.MessageBuffer.isColoringDefault` is used.
        :type isColoring: bool | None

        :return: The concatenated `str`, with individual messages separated by 
            newline characters.
        :rtype: str
        """

        outStream = StringIO()
        self.printText(
            lngForText=lngForText,
            severityFilter=severityFilter,
            correlationIdFilter=correlationIdFilter,
            isColoring=isColoring,
            stdStream=outStream,
            errStream=None
        )

        return outStream.getvalue().strip()

    def getExitCode(self):
        """Determines the most appropriate numeric exit code for the process,
        based on the messages in the buffer.

        | If instances of :class:`~.ErrorMessage` were appended to this buffer,
          the :attr:`~.ErrorMessage.exitCode` of the instance appended last is
          returned.
        | Otherwise, the exit code depends on the message in the buffer with the
          "highest" severity:
        | ``4`` if at least one message has the severity
          :attr:`~.Severity.ERROR`.
        | ``3`` if at least one message has the severity
          :attr:`~.Severity.WARNING`.
        | ``0``  otherwise.

        :return: The numeric exit code.
        :rtype: int
        """

        if self.lastErrorExitCode is not None:
            return self.lastErrorExitCode

        if not self:
            return 0

        if self.hasSeverity(Severity.ERROR):
            return 4

        if self.hasSeverity(Severity.WARNING):
            return 3

        return 0

    def hasKey(self, key, argumentFilter=None, correlationId=None):
        """Searches the buffer for a message with a certain key.

        :param key: The key to search for.
        :type key: str
        :param argumentFilter: An optional filter for messages by argument. See
            :paramref:`~.MessageIterator.argumentFilter`.
        :type argumentFilter: object | dict | None
        :param correlationIdFilter: An optional filter for messages by 
            correlation ID. See :paramref:`~.MessageIterator.correlationIdFilter`.
        :type correlationIdFilter: str | None

        :return: `True` if at least one message has been found.
        :rtype: bool
        """

        for msgObject in MessageIterator(
            self, format=MessageIterator.Format.OBJECT,
            argumentFilter=argumentFilter, correlationIdFilter=correlationId
        ):
            if msgObject.key == key:
                return True

        return False

    def hasSeverity(self, severities, argumentFilter=None, correlationId=None):
        """Searches the buffer for a message with one of several possible
        severities.

        :param severities: One or more severities to search for. It is
            sufficient for a message to match one of the given severities.
        :type severities: list | tuple
        :param argumentFilter: An optional filter for messages by argument. See
            :paramref:`~.MessageIterator.argumentFilter`.
        :type argumentFilter: object | dict | None
        :param correlationIdFilter: An optional filter for messages by 
            correlation ID. See :paramref:`~.MessageIterator.correlationIdFilter`.
        :type correlationIdFilter: str | None

        :return: `True` if at least one message has been found.
        :rtype: bool
        """

        if type(severities) not in (Severity, tuple, list):
            raise ValueError("severities must be of format Severity or a tuple or list thereof.")

        if isinstance(severities, Severity):
            severities = (severities,)

        for msgObject in MessageIterator(
            self, format=MessageIterator.Format.OBJECT,
            argumentFilter=argumentFilter, correlationIdFilter=correlationId
        ):
            if msgObject.severity in severities:
                return True

        return False

    def addTranslations(
        self,
        isOkLoadError=True,
        isOkKeyError=True,
        isOkInterpolationError=False
    ):
        """Adds translations for the message texts already in the buffer. 
        
        The target language is set in the global scope of the Python process via
        :meth:`~.TranslationManager.changeLanguage`.
        
        :param isOkLoadError: If `False`, a message with key
            `TRANSLATION_LOAD_FAILED` and severity :attr:`~.Severity.WARNING`
            is appended to the buffer if a translation file for the target
            language cannot be loaded. If `True`, the message is appended with
            severity :attr:`~.Severity.DEBUG`.
        :type isOkLoadError: bool
        :param isOkKeyError: If `False`, a message with key
            `TRANSLATION_KEY_NOT_FOUND` and severity :attr:`~.Severity.WARNING`
            is appended to the buffer if a specific translation key cannot be
            found. If `True`, the message is appended with severity
            :attr:`~.Severity.DEBUG`.
        :type isOkKeyError: bool
        :param isOkInterpolationError: If `False`, a message with key
            `TRANSLATION_INTERPOLATION_FAILED` and severity
            :attr:`~.Severity.WARNING` is appended to the buffer if the value
            for a variable cannot be resolved. If `True`, the message is
            appended with severity :attr:`~.Severity.DEBUG`.
        """

        msgObjects = MessageIterator(self, severityFilter=None, isImmutable=True)
        # Create a new buffer with translated messages.
        self.clear()

        for msgObject in msgObjects:
            if not hasattr(msgObject, 'text%s' % Translation.language.capitalize()):
                translation = None
                debugOutput = StringIO()
                try:
                    translation = Translation.translate(
                        msgObject.key, msgObject.arguments,
                        debugOutput=debugOutput
                    )
                except (FileNotFoundError, json.JSONDecodeError) as error:
                    self.append(
                        "Error loading language %s: %s" % (Translation.language, str(error)),
                        'TRANSLATION_LOAD_FAILED', (error, Translation.language),
                        Severity.DEBUG if isOkLoadError else Severity.WARNING
                    )
                except KeyError as error:
                    self.append(
                        str(error).strip('"'), 'TRANSLATION_KEY_NOT_FOUND',
                        (msgObject.key, Translation.language),
                        Severity.DEBUG if isOkKeyError else Severity.WARNING
                    )

                debugMsg = debugOutput.getvalue()
                if debugMsg:
                    self.append(
                        debugMsg, 'TRANSLATION_INTERPOLATION_FAILED',
                        (msgObject.key, Translation.language),
                        Severity.DEBUG if isOkInterpolationError else Severity.WARNING
                    )

                if translation is not None:
                    setattr(msgObject, 'text%s' % Translation.language.capitalize(), translation)
            self.append(msgObject, isDeduplicate=False)


class LoggingMessageBuffer(MessageBuffer):
    """A message buffer capable of logging messages to a file."""

    def __init__(
        self,
        loop,
        nsDefault,
        lngDefault='en',
        isColoringDefault=None,
        logPath=None,
        firstColumnWidth=24,
        isLogImmediately=True,
        isLogToConsole=False
    ):
        """Creates a new logging message buffer.
        
        :param nsDefault: See :attr:`~.MessageBuffer.nsDefault`.
        :type nsDefault: str
        :param lngDefault: See :attr:`~.MessageBuffer.lngDefault`.
        :type lngDefault: str
        :param isColoringDefault: See :attr:`~.MessageBuffer.isColoringDefault`.
        :type isColoringDefault: bool | None
        :param logPath: See :attr:`~.LoggingMessageBuffer.logPath`.
        :type logPath: str
        :param firstColumnWidth: The width of the first column in the log 
            output, as number of characters.
        :type firstColumnWidth: int
        :param isLogImmediately: See :attr:`~.LoggingMessageBuffer.isLogImmediately`.
        :type isLogImmediately: bool
        :param isLogToConsole: See :attr:`~.LoggingMessageBuffer.isLogToConsole`.
        :type isLogToConsole: bool
        """

        super().__init__(
            nsDefault,
            lngDefault=lngDefault,
            isColoringDefault=isColoringDefault
        )

        self.loop = loop

        self.logPath: str = logPath
        """The log file path. It is only opened when
        :meth:`~.LoggingMessageBuffer.openLog` is called.
        """

        self.firstColumnFormatStr: str = '%-' + str(firstColumnWidth) + 's' if firstColumnWidth is not None else None
        """The printf-style string used to format the first column in the log
        output.

        The first column contains the timestamp of the corresponding log entry,
        right-padded with spaces to match the number of characters given in
        :paramref:`~.LoggingMessageBuffer.firstColumnWidth`.
        """

        self.isLogImmediately: bool = isLogImmediately
        """If `True`, any message is written to the log file as soon as it is
        appended to the buffer. If `False`, all the messages in the buffer are
        written to the log file when the buffer is closed via its
        :meth:`~.LoggingMessageBuffer.close` method.
        """

        self.isLogToConsole: bool = isLogToConsole
        """If `True`, messages are additionally printed to *stdout* at the same
        time when they are written to the log file.
        """

        self.logHandle = None

    async def openLog(
        self,
        logPath=None,
        firstColumnWidth=None
    ):
        """Actually opens the log file. If it exists already, log entries will
        be appended to it. Otherwise the file is created.

        :param logPath: The log file path to open and to set as the value of
            :attr:`~.LoggingMessageBuffer.logPath`. If `None`, the existing
            value of :attr:`~.LoggingMessageBuffer.logPath` is used.
        :type logPath: str
        :param firstColumnWidth: The width of the first column in the log 
            output, as a number of characters. If `None`, the value given in
            :paramref:`~.LoggingMessageBuffer.firstColumnWidth` is retained.
        :type firstColumnWidth: int

        :raise ValueError: If no log file path is given.
        """

        if not logPath and not self.logPath:
            raise ValueError("At least logPath must be given.")

        if logPath is not None:
            self.logPath = logPath
        if firstColumnWidth is not None:
            self.firstColumnFormatStr = '%-' + str(firstColumnWidth) + 's'

        self.logHandle = await aiofiles.open(self.logPath, 'a', buffering=1, loop=self.loop)

    async def _logEn(self, msgObject):
        isColoring = self.isColoringDefault

        if msgObject.timestamp_iso is None:
            timestamp = datetime.now(tz=None)
        elif isinstance(msgObject.timestamp, str):
            timestamp = datetime.fromisoformat(msgObject.timestamp_iso)
        elif not isinstance(msgObject.timestamp, datetime):
            raise ValueError("timestamp must be a string in ISO datetime format or an instance of datetime.")

        timestampStr = timestamp.strftime(LoggingMessageBuffer.LOG_TIME_FORMAT_PATTERN)
        text = msgObject.toColoredText() if isColoring else msgObject.toText()
        logEntry = "%s%s" % (self.firstColumnFormatStr % timestampStr, text)

        if self.isLogToConsole:
            print(logEntry)
        await self.logHandle.write("%s\n" % logEntry)

    def append(
        self,
        msg,
        key=None,
        arguments=(),
        severity: Severity=Severity.NORMAL,
        timestamp_iso=None,
        ns=None,
        correlationId=None,
        isDeduplicate=None
    ):
        """Immediately writes a message to the log file if
        :attr:`~.LoggingMessageBuffer.isLogImmediately` is `True` and a
        a log file has been opened via :meth:`~.LoggingMessageBuffer.openLog`.
        In this case, messages are not buffered. Hence, they cannot be queried
        programatically via :meth:`~.LoggingMessageBuffer.hasKey` or
        :meth:`~.LoggingMessageBuffer.hasSeverity` or any other method.

        In all other cases, the call is delegated to the super method
        :meth:`~.MessageBuffer.append`.
        """

        super().append(
            msg, key, arguments, severity, timestamp_iso, ns, correlationId
        )

        if self.logHandle and self.isLogImmediately:
            logTask = self.loop.create_task(
                self._logEn(self.msgObjects.pop(0)),
                name='logger-write'
            )

    def close(self):
        """Closes the buffer, eventually writing all messages in the buffer
        to the log file if :attr:`~.isLogImmediately` is `False`.
        """

        if self.logHandle and not self.isLogImmediately:
            for msgObject in self:
                self._logEn(msgObject)

        if self.logHandle and self.logHandle.fileno() != sys.stdout.fileno():
            self.loop.run_until_complete(self.logHandle.close())
        self.logHandle = None

    LOG_TIME_FORMAT_PATTERN = '%a %d.%m.%Y %H:%M:%S.%f'


class MessageIterator():
    """An iterator over an ordered sequence of messages.

    While iterating, messages may still be appended to the buffer.
    """

    class Format(Enum):
        """The output format of messages."""

        OBJECT = 0
        """Instances of :class:`~.Message`."""

        TEXT = 2
        """The message texts as `str`."""

        TEXT_COLORED = 3
        """The message texts as `str` enclosed in ANSI control characters
        coloring the text according to severity.
        """

    def __init__(
        self,
        messages,
        format=Format.OBJECT,
        startIndex=0,
        isImmutable=False,
        argumentFilter=None,
        severityFilter=SEVERITY_FILTER_DEFAULT,
        correlationIdFilter=None,
        lngForText='en'
    ):
        """
        :param messages: The messages to iterate over.
        :type messages: MessageBuffer | list[Message] | tuple[Message]
        :param format: The output format of the messages.
        :type format: :class:`~.MessageIterator.Format`
        :param startIndex: The (zero-based) index of the first message to be
            included in the output. It must be less than ``len(messages)``. As
            an example, if  `startIndex` is ``2``, iteration starts with the
            third message in :paramref:`~.MessageIterator.messages`.
        :type startIndex: int
        :param isImmutable: If `True`, the iterator uses an immutable copy of
            the message sequence. In this case, messages appended afterwards are
            not reflected in the iterator.
        :type isImmutable: bool
        :param argumentFilter: An optional filter for message arguments. The
            filter is specified as a scalar value, e.g. as a `str`, `int` or 
            `bool`. The value must exactly match the value of one of a message's
            arguments to be included in the output. Alternatively, the filter is
            specified as a `dict` with a single key/value pair. As an example,
            ``argumentFilter={'retryCount':5}`` matches message arguments
            specified as a `dict`, containing a semantic key ``retryCount``
            with a value of ``5``.If `None`, messages are not filtered by 
            arguments.
        :type argumentFilter: object | dict | None
        :param severityFilter: An optional filter for message severities. The
            filter is specified as an `Iterable` with one or more severities. At
            least one of them must match the severity of a message for it to be
            included in the output. If `None`, messages are not filtered by
            severity.
        :type severityFilter: Iterable | None
        :param correlationIdFilter: An optional filter for correlation IDs. The
            value must exactly match the correlation ID of a message for it to
            be included in the output. If `None`, messages are not filtered by
            correlation ID.
        :type correlationIdFilter: str | None
        :param lngForText: Two-letter language code for the message texts to
              be included in the output. Defaults to ``en``, if a corressponding
              translation cannot be found. Only relevant for output formats
              :attr:`~.MessageIterator.Format.TEXT` and 
              :attr:`~.MessageIterator.Format.TEXT_COLORED`.
        :type lngForText: str

        :raise ValueError: If :paramref:`~.MessageIterator.startIndex` is
            invalid.
        :raise ValueError: If :paramref:`~.MessageIterator.argumentFilter` is
            an invalid `dict`.
        """

        self.format = format
        self.isImmutable = isImmutable

        if isinstance(messages, MessageBuffer):
            self.messages = messages
            self.msgObjects = self.messages.getList(correlationIdFilter=None)
        elif type(messages) in (list, tuple):
            self.messages = None
            self.msgObjects = messages
        else:
            raise ValueError("messages must be of type MessageBuffer or a sequence of Message instances.")

        if self.isImmutable:
            self.msgObjects = copy.copy(self.msgObjects)

        if startIndex > len(self.msgObjects):
            raise ValueError("startIndex must not point beyond the last message in the buffer.")
        elif startIndex < 0:
            raise ValueError("startIndex may not be negative.")

        if argumentFilter is not None:
            if type(argumentFilter) in (list, tuple, dict) and len(argumentFilter) != 1:
                raise ValueError("If argumentFilter is a %s, it must have exactly one entry." % str(type(argumentFilter)))
            if type(argumentFilter) in (list, tuple):
                argumentFilter = argumentFilter[0]
        self.argumentFilter = argumentFilter

        self.severityFilter = severityFilter
        self.correlationIdFilter = correlationIdFilter.strip() if correlationIdFilter else None
        self.changeLanguageForText(lngForText)
        self.lastIndex = startIndex - 1
        self.lastCount = len(self.msgObjects)
        self.isStopped = False

    def __iter__(self):
        return self

    def __next__(self):
        if self.isStopped:
            raise StopIteration()

        count = len(self.messages) if self.messages is not None else len(self.msgObjects)
        if not self.isImmutable and count < self.lastCount:
            # Message buffer has been cleared, so reset the cursor.
            self.lastIndex = -1
        self.lastCount = count

        nextIndex = self.getNextIndex()

        nextItem = self.renderItem(nextIndex)
        if nextItem is None:
            self.isStopped = True
            raise StopIteration()

        self.lastIndex = nextIndex
        return nextItem

    def getNextIndex(self):
        if not self.isImmutable and self.messages is not None:
            self.msgObjects = self.messages.getList()

        increment = 1
        if not any((self.severityFilter, self.correlationIdFilter, self.argumentFilter)):
            if self.lastIndex + increment < len(self.msgObjects):
                return self.lastIndex + increment
            return None

        while self.lastIndex + increment < len(self.msgObjects):
            msgObject = self.msgObjects[self.lastIndex + increment]
            if isinstance(msgObject, dict):
                msgObject = Message.fromDict(msgObject)

            if self.severityFilter and msgObject.severity not in self.severityFilter:
                increment += 1
                continue

            if self.correlationIdFilter and (
                not msgObject.correlationId or \
                msgObject.correlationId != self.correlationIdFilter
            ):
                increment += 1
                continue

            if self.argumentFilter:
                if isinstance(self.argumentFilter, dict):
                    key, value = tuple(self.argumentFilter.items())[0]
                else:
                    key, value = (None, self.argumentFilter)

                if isinstance(msgObject.arguments, list) or isinstance(msgObject.arguments, tuple):
                    if not value in msgObject.arguments:
                        increment += 1
                        continue
                elif not isinstance(msgObject.arguments, dict):
                    if key:
                        if key not in msgObject.arguments or msgObject.arguments[key] != value:
                            increment += 1
                            continue
                    else:
                        if not value in msgObject.arguments.values():
                            increment += 1
                            continue
                elif value != msgObject.arguments:
                    increment += 1
                    continue

            return self.lastIndex + increment
        return None

    def renderItem(self, index):
        if not self.isImmutable and self.messages is not None:
            self.msgObjects = self.messages.getList()

        if index is None:
            return None

        msgObject = self.msgObjects[index]
        if isinstance(msgObject, dict):
            msgObject = Message.fromDict(msgObject)

        if self.format == MessageIterator.Format.OBJECT:
            return msgObject
        if self.format == MessageIterator.Format.TEXT:
            return msgObject.toText(lng=self.lngForText)
        if self.format == MessageIterator.Format.TEXT_COLORED:
            return msgObject.toColoredText(lng=self.lngForText)

    def changeLanguageForText(self, lngForText):
        self.lngForText = lngForText


class MessageHelper():
    """Static functions for dealing with messages."""

    MESSAGES_PATTERN = re.compile(r'"messages":\s*(\[\s*(?:\{\n?([\ \t]+).+?\})*\s*\])', flags=re.MULTILINE | re.DOTALL)
    INDENT_PATTERN = re.compile(r'(\n)?(.)', flags=re.MULTILINE)

    @staticmethod
    def getSpaceIndent(indent=4):
        return ('%' + str(indent) + 's') % ' '

    @staticmethod
    def getColoredText(text, color):
        if isinstance(color, Severity):
            colorCode = color.value
        elif isinstance(color, int) or (isinstance(color, str) and color.isdigit()):
            colorCode = int(color)
        else:
            raise ValueError("color must either be of type Severity or an integer.")

        output = StringIO()

        output.write('\033[0;%im' % colorCode)
        output.write(text)
        output.write('\033[0m')

        return output.getvalue().strip()

    @staticmethod
    def indentLines(jsonStr, levels=1, indent=4):
        indentStr = MessageHelper.getSpaceIndent(indent)

        output = StringIO()
        # First remove all indentation:
        jsonStr = re.sub(r'^\s*', '', jsonStr, flags=re.MULTILINE)

        lastEnd = 0
        level = levels
        isInQuote = False
        # Iterate over curly braces, brackets and double quotes and indent at new lines depending on the current indent level.
        for match in MessageHelper.INDENT_PATTERN.finditer(jsonStr):
            isLineStart = bool(match.group(1))
            char = match.group(2)

            if char in ('}', ']'):
                level -= 1

            output.write(jsonStr[lastEnd:match.start(2)])

            if not isInQuote and isLineStart:
                for i in range(0, level):
                    output.write(indentStr)

            output.write(char)
            lastEnd = match.end(2)

            if char == '"':
                isInQuote = not isInQuote
            elif char in ('{', '['):
                level += 1

        output.write(jsonStr[lastEnd:])

        return output.getvalue()

    @staticmethod
    def escape(argument):
        if isinstance(argument, Number) or isinstance(argument, bool):
            return argument
        argument = str(argument)

        outStr = argument.replace('\\', '\\\\')
        outStr = outStr.replace('"', '\\"')
        outStr = outStr.replace('\n', '\\n')
        return outStr

    @staticmethod
    def getJsonLiteral(argument):
        output = StringIO()

        if argument is None:
            output.write('null')
        elif isinstance(argument, Message):
            output.write(argument.toJson())
        elif isinstance(argument, dict):
            output.write('{')
            i = 0
            for key, value in argument.items():
                output.write('\n')
                output.write(MessageHelper.getJsonLiteral(key))
                output.write(': ')
                output.write(MessageHelper.getJsonLiteral(value))
                if i < len(argument) - 1:
                    output.write(',')
                i += 1

            if len(argument):
                output.write('\n')
            output.write('}')
        elif type(argument) in (list, tuple):
            output.write('[')
            for i in range(0, len(argument)):
                element = argument[i]

                output.write('\n')
                literal = MessageHelper.getJsonLiteral(element)
                if literal:
                    output.write(literal)
                if i < len(argument) - 1:
                    output.write(',')

            if len(argument):
                output.write('\n')
            output.write(']')
        else:
            isRenderedAsStr = not isinstance(argument, Number) and not isinstance(argument, bool)

            if isRenderedAsStr:
                output.write('"')
            if isinstance(argument, Enum):
                output.write(MessageHelper.escape(argument.name))
            elif isinstance(argument, bool):
                output.write('true' if argument else 'false')
            else:
                output.write(str(MessageHelper.escape(argument)))
            if isRenderedAsStr:
                output.write('"')

        return output.getvalue()

    @staticmethod
    def nameJson(jsonStr, name, indent=4):
        indentStr = MessageHelper.getSpaceIndent(indent)
        output = StringIO()
        output.write(indentStr)
        output.write('"')
        output.write(name)
        output.write('": ')
        output.write(MessageHelper.indentLines(jsonStr, indent=indent).strip())
        return output.getvalue()

    # Removes the "message" tag and brackets.

    @staticmethod
    def unpackMessages(jsonStr):
        if not jsonStr:
            return jsonStr

        match = MessageHelper.MESSAGES_PATTERN.search(jsonStr)
        if not match:
            return jsonStr
        return match.group(1)

    @staticmethod
    def packMessageAndStatusJson(jsonStr, statusJson=None, indent=4):
        output = StringIO()
        output.write('{\n')
        if statusJson and statusJson.strip('{}'):
            output.write(MessageHelper.nameJson(statusJson, 'status', indent=indent))
        if jsonStr and jsonStr.strip('[] \n'):
            if statusJson:
                output.write(',\n')
            output.write(MessageHelper.nameJson(jsonStr, 'messages', indent=indent))
        output.write('\n}')
        return output.getvalue()

