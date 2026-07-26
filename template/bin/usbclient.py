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

import os

import asyncio

import time
from enum import Enum
import re

from ctlbase.control import Control
from ctlbase.message import Severity, ErrorMessage
from ctlbase.process import CommandExecutor


class UsbClient(Control):

    class Action(Enum):
        ATTACH = 'attach'
        DETACH = 'detach'
        REATTACH = 'reattach'
        MODULE_LOAD = 'module-load'

    class Property(Enum):
        USB_VENDOR_PRODUCT = 'vendor-product'
        REMOTE_BUS_ID = 'remote-bus-id'
        MODULE_IS_LOADED = 'module-is-loaded'
        IS_ATTACHED = 'is-attached'
        USBIP_PORT = 'port'

    def __init__(
        self,
        loop,
        commandExecutor=None,
        usbId=None,
        envConfig=True,
        msgBuffer=None,
        jsonIndent=4
    ):
        super().__init__(
            loop,
            commandExecutor=commandExecutor,
            envConfig=envConfig,
            actionType=UsbClient.Action,
            modeType=None,
            propertyType=UsbClient.Property,
            msgBuffer=msgBuffer,
            jsonIndent=jsonIndent
        )

        if not usbId:
            raise ValueError('usbId must be a str in the format <vendorId>:<productId>, e.g. 03F0:4000')

        self.usbVendorId, self.usbProductId = usbId.split(':')
        self.hostname = self.envConfigDict['DEVICE_HOST_NAME']
        self.reattachPause_ms = int(self.envConfigDict['REATTACH_PAUSE_MS']) if 'REATTACH_PAUSE_MS' in self.envConfigDict else None

        self._invalidateCache()

    async def getRemoteUsbBusId(self):
        if self.usbBusIdCached is not None:
            return self.usbBusIdCached

        try:
            usbipStr = await self.commandExecutor.executeCommands(
                ('usbip', 'list', '--remote', self.hostname),
                isSynchronous=True, isLogEnabled=False
            )

            if not usbipStr:
                return None

            match = re.search(r'^\s*(\d+-\d+\.\d+):\s*(.+?)\s*\(%s:%s\)' % (self.usbVendorId, self.usbProductId), usbipStr, flags=re.MULTILINE)
            if match:
                self.usbBusIdCached = match.group(1)
                return self.usbBusIdCached
        except CommandExecutor.SynchronousError as error:
            raise ErrorMessage(
                "Exit code %s getting USB bus ID: %s" % (error.returncode, error.message),
                'REMOTE_BUS_ID_GET_FAILED', (error.returncode, error.message)
            )

        return None

    async def isClientModuleLoaded(self):
        try:
            lsmodStr = await self.commandExecutor.executeCommands(
                'lsmod', isSynchronous=True, isLogEnabled=False
            )
            return bool(re.search(r'\bvhci_hcd\b', lsmodStr, flags=re.MULTILINE))
        except CommandExecutor.SynchronousError as error:
            raise ErrorMessage(
                "Exit code %s getting the kernel module state: %s" % (error.returncode, error.message),
                'MODULE_IS_LOADED_GET_FAILED', (error.returncode, error.message)
            )

    async def getUsbipPort(self):
        if self.usbipPortCached is not None:
            return self.usbipPortCached

        try:
            usbipStr = await self.commandExecutor.executeCommands(
                ('usbip', 'port'), isSynchronous=True, isLogEnabled=False
            )

            if not usbipStr:
                return None

            match = re.search(r'^Port\s+(\d+):.+?\(%s:%s\)' % (self.usbVendorId, self.usbProductId), usbipStr, flags=re.MULTILINE|re.DOTALL)
            if match:
                self.usbipPortCached = match.group(1)
                return self.usbipPortCached
        except CommandExecutor.SynchronousError as error:
            raise ErrorMessage(
                "Exit code %s getting the usbip port: %s" % (error.returncode, error.message),
                'USBIP_PORT_GET_FAILED', (error.returncode, error.message)
            )

    async def isAttached(self):
        usbipPort = await self.getUsbipPort()
        return usbipPort is not None

    async def getUdevInfo(self, devicePath):
        identifierArgument = '--path' if devicePath.startswith('/sys/') else '--name'

        try:
            return await self.commandExecutor.executeCommands(
                ('udevadm', 'info', '--query', 'property', identifierArgument, devicePath,),
                isSynchronous=True,
                isLogEnabled=False
            )
        except CommandExecutor.SynchronousError as error:
            if error.returncode > 1:
                # 1: Unknown device: Inappropriate ioctl for device.
                raise error

        return None

    async def performRemoteAction(self, action, credential=None):
        if action == self.actionType.DETACH:
            port = await self.getUsbipPort()
            if credential is not None:
                usbipStr = await self.commandExecutor.executeCommands(
                    ('sudo', '--user=root', '--stdin', 'usbip', 'detach', '--port', port),
                    stdInput=credential, isSynchronous=True, isLogEnabled=False
                )
            else:
                usbipStr = await self.commandExecutor.executeCommands(
                    ('sudo', '--user=root', 'usbip','detach', '--port', port),
                    isSynchronous=True, isLogEnabled=False
                )
        elif action == self.actionType.ATTACH:
            # if ! usbip_output="$( echo "${temp1}" | sudo --user=root --stdin $USBPIP_EXECUTABLE attach --remote $HOST_NAME --busid $DEVICE_BUS_ID >/dev/null )"; then
            usbBusId = await self.getRemoteUsbBusId()

            if usbBusId is None:
                raise ErrorMessage(
                    "Cannot attach the USB device when it is not bound to USB/IP on the host.",
                    'ATTACH_FAILED', ()
                )

            if credential is not None:
                usbipStr = await self.commandExecutor.executeCommands(
                    ('sudo', '--user=root', '--stdin', 'usbip', 'attach', '--remote', self.hostname, '--busid', usbBusId),
                    stdInput=credential, isSynchronous=True, isLogEnabled=False
                )
            else:
                usbipStr = await self.commandExecutor.executeCommands(
                    ('sudo', '--user=root', 'usbip', 'attach', '--remote', self.hostname, '--busid', usbBusId),
                    isSynchronous=True, isLogEnabled=False
                )

        self._invalidateCache()

    def _actionToGrammarEn(self, action, argument, feedback):
        infinitive, presentParticiple, pastParticiple, object, addendum = super()._actionToGrammarEn(action, argument, feedback)

        if action == self.actionType.MODULE_LOAD:
            object = "the USB/IP client module"
        else:
            object = "the USB device"

        return infinitive, presentParticiple, pastParticiple, object, addendum

    async def _performAction(
        self,
        requestedAction,
        argument=None,
        mayPrompt=None,
        isOkNodo=False
    ):
        actions = [requestedAction]

        if self.actionType.REATTACH in actions:
            actions = [self.actionType.DETACH, self.actionType.ATTACH]

        if self.actionType.ATTACH in actions:
            # Loading the USB/IP client module must be the first action.
            actions.insert(0, self.actionType.MODULE_LOAD)

        for action in actions:
            isRequested = (requestedAction == action)

            if (action == self.actionType.MODULE_LOAD and await self.isClientModuleLoaded()) or \
                (action == self.actionType.DETACH and not await self.isAttached()) or \
                (action == self.actionType.ATTACH and await self.isAttached()):
                self._appendMessageFromActionEn(
                    action, argument, 'already',
                    isOkNodo=(not isRequested)
                )
                if isRequested:
                    return None
            elif action == self.actionType.MODULE_LOAD:
                await self.commandExecutor.executeCommands(
                    ('sudo', '--user=root', 'modprobe', 'vhci_hcd'),
                    isSynchronous=True, isLogEnabled=False
                )
            else:
                if requestedAction == self.actionType.REATTACH and self.reattachPause_ms:
                    await asyncio.sleep(float(self.reattachPause_ms) / 1000)
                await self.performRemoteAction(action)

        return requestedAction, argument, None

    def _invalidateCache(self):
        self.usbBusIdCached = None
        self.usbipPortCached = None

    async def _getProperty(self, property):
        propertyMember = self.propertyType.fromAny(property)

        if propertyMember == self.propertyType.USB_VENDOR_PRODUCT:
            return '%s:%s' % (self.usbVendorId, self.usbProductId)
        elif propertyMember == self.propertyType.REMOTE_BUS_ID:
            return await self.getRemoteUsbBusId()
        elif propertyMember == self.propertyType.MODULE_IS_LOADED:
            return await self.isClientModuleLoaded()
        elif propertyMember == self.propertyType.USBIP_PORT:
            return await self.getUsbipPort()
        elif propertyMember == self.propertyType.IS_ATTACHED:
            return await self.isAttached()

    async def _getPropertiesListsEn(self):
        propertiesListsEn = await super()._getPropertiesListsEn()
        propertiesListsEn[0][2] = 'USB vendor:product'
        propertiesListsEn[1][2] = 'remote USB bus ID'
        propertiesListsEn[2][2] = 'module is loaded'
        propertiesListsEn[4][2] = 'local USB/IP port'
        return propertiesListsEn
