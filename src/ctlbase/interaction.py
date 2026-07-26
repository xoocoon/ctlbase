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

"""Classes for basic interaction with the user."""

import os

import re
import getpass

from ctlbase.config import TRUE_VALUE_PATTERN, FALSE_VALUE_PATTERN
from ctlbase.message import Severity, ErrorMessage, MessageHelper
from ctlbase.translation import Translation
from ctlbase.process import CleaningUp


COLOR_PRINT_ENVKEY = 'COLOR_PRINT'
"""Environment configuration key for the coloring of interactive prompts printed
to *stdout*.
"""


class InteractionManager:
    """A manager for user interactions."""

    def __init__(self, isColoringDefault=None):
        """Creates an interaction manager with a specific configuration.
        
        :param isColoringDefault: If `True`, prompts are colored by default when
            printed to *stdout*. If `None`, a possibly existing 
            :data:`~.COLOR_PRINT_ENVKEY` environment value is used. If the
            latter does not exist, a default value of `True` is assumed.
        :type isColoringDefault: bool | None
        """

        self.isColoringDefault = isColoringDefault
        """If `True`, prompts are colored by default when printed to *stdout*."""

        if isColoringDefault is None and 'COLOR_PRINT' in os.environ:
            if TRUE_VALUE_PATTERN.search(os.environ['COLOR_PRINT']):
                self.isColoringDefault = True
            elif FALSE_VALUE_PATTERN.search(os.environ['COLOR_PRINT']):
                self.isColoringDefault = False
        elif isColoringDefault is None:
            self.isColoringDefault = True

        self.lastPurpose = None

    def promptForCredential(self, purpose=None, isColoring=None):
        """Prompts the user for a credential. The characters entered are not
        shown.
        
        The prompt can be translated via the message key
        `INTERACTION_CREDENTIAL_PROMPT`, including a variable named `purpose`.
        
        :param purpose: A language-agnostic `str` to present to the user as the 
            purpose of the requested credentials. Example: ``HomeServer via SMB``
        :type purpose: str
        :param isColoring: If `True`, the prompt is colored when printed to
            *stdout*. If `None`, the value of
            :attr:`~.InteractionManager.isColoringDefault` is used.
        :type isColoring: bool | None
        
        :raise ErrorMessage: `INTERACTION_PROMPT_ABORTED` – If entering the
            credential was aborted by the user.
        
        :return: The entered credential.
        :rtype: str
        """

        try:
            prompt = Translation.translate('INTERACTION_CREDENTIAL_PROMPT', {'purpose': purpose})
        except:
            if purpose:
                prompt = "Please enter the credential for %s:" % purpose
            else:
                prompt = "Please enter the credential:"

        if isColoring or (isColoring is None and self.isColoringDefault):
            prompt = MessageHelper.getColoredText(prompt, Severity.WARNING)
        print(prompt, end=" ", flush=True)

        try:
            return getpass.getpass(prompt="")
        except CleaningUp:
            print()
            raise ErrorMessage("Password request aborted.", 'INTERACTION_PROMPT_ABORTED')


Interaction = InteractionManager()
"""Singleton for interacting with the user."""
