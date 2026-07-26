#!/usr/bin/env python3

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

import argparse

from ctlbase.shell import ControlShell
from ctlbase.message import Severity, ErrorMessage
from usbclient import UsbClient


# For more information on the Linux implementation of USB/IP, see 
# https://github.com/torvalds/linux/tree/master/tools/usb/usbip.
parser = argparse.ArgumentParser(description="Control an USB device connected to a remote PC.")

subparsers = parser.add_subparsers(dest='action')

# Group all actions into one sub parser that need the USB device as an argument.
for action in ('attach', 'detach', 'reattach', 'status'):
    subparser = subparsers.add_parser(action)
    # The '[]' syntax interpolates a default value from a config file if no 
    # command # line argument is provided.
    subparser.add_argument('-d', '--device', default='[DEVICE_ID]', help='Identifier for the USB device on the host in the format <vendorId>:<productId>, e.g. 03F0:4000')

ControlShell.addStandardArgs(parser)


class UsbClientShell(ControlShell):

    async def main(self):
        # If the 'status' action is requested, distinguish between JSON and
        # plain text output.
        if self.args.action == 'status':
            # The 'json' flag comes from the standard arguments.
            if self.args.json:
                self.printOnExit(await self.control.getPropertiesJson())
            else:
                self.printOnExit(await self.control.getPropertiesTextEn())
        else:
            # For all other actions than 'status', call the performAction() 
            # method of the control, in this case an instance of UsbClient.
            kwargs = self.standardToOperateArgs()
            await self.control.performAction(self.args.action, None, **kwargs)


def main():
    # Create a shell, i.e. a context manager taking care of all the required
    # resources.
    with UsbClientShell(
        parser,                 # Parser for the command line arguments.
        commandExecutor=True,   # Flag to auto-create an executor for usbip commands.
        envConfigName=True      # Flag to auto-discover a config file named 'usbclient'.
    ) as shell:
        # Create an instance of UsbClient and set it as the control of the 
        # shell. At this point, the control can already use the resources 
        # managed by the shell, e.g. the command executor.
        shell.setControl(
            UsbClient(
                shell.loop,     # asyncio event loop auto-created by the shell.
                commandExecutor=shell.commandExecutor,
                usbId=shell.args.device if hasattr(shell.args, 'device') else None,
                envConfig=(shell.envConfigName, shell.envConfigDict), 
                msgBuffer=True  # Flag to auto-create a message buffer.
            )
        )

        # Execute the shell's main function to actually perform the action(s)
        # requested on the command line.
        shell.loop.run_until_complete(shell.main())

if __name__ == '__main__':
    main()
