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

"""Simplistic internationalization classes inspired by i18next."""

import sys
import os
import locale

import time
from io import StringIO
import json
import re

from ctlbase.config import FileHelper


class TranslationManager():
    """A translation manager for various languages.
    
    For each target language, a JSON-formatted translation file must exist. The
    following is an example for ``de``::
    
        {
            "defaultVariables": {
                "source": "Signalquelle"
            },
            "SOURCE_SET_SUCCEEDED": "{{source}} gewählt.",
            "THREED_ON_SUCCEEDED": "3D-Modus eingeschaltet.",
            "THREED_OFF_SUCCEEDED": "3D-Modus ausgeschaltet.",
            "MODE_SET_SUCCEEDED": "Modus {{mode}} für {{timeout}} Sekunden gesetzt.",
            "MODE_UNSET_SUCCEEDED": "Modus {{mode}} zurückgenommen."
        }
    
    The syntax is a basic version of i18next. Each translation in the file is
    identified by an individual key. What is more, each translation may contain
    one or more variable names enclosed in double curly braces.
    
    A variable name in turn can either be a numeric index or a semantic key::

        "Modus {{0}} für {{1}} Sekunden gesetzt."
        # or
        "Modus {{mode}} für {{timeout}} Sekunden gesetzt."
    
    At runtime, variables are replaced by their actual values. If, during the
    so-called interpolation, the value of an individual variable cannot be
    resolved, a possibly existing default value from the ``defaultVariables``
    object is used.
    
    Only one target language is active at a time. It can be changed via 
    :meth:`~.TranslationManager.changeLanguage`. The actual translation of a `str` is
    done via :meth:`~.TranslationManager.translate`.
    """

    VARIABLE_PATTERN = re.compile(r'(\{\{(?:\d{1,2}|\w{2,16})\}\})')

    def __init__(self, lng):
        """Creates a translation manager with an initial target language.
        
        :param lng: Two-letter language code for the initial target language.
        :type lng: str
        """

        self.translationPath = {}
        self.translationDict = {}
        
        self.language: str = None
        """Two-letter language code for the target language."""

        self.changeLanguage(lng)

    def loadLanguages(self, lngs, isForceReload=False):
        """(Pre)loads the translation files for one or more languagea. 
        
        The search path is determined by
        :attr:`~.FileHelper.TRANSLATION_LOCATIONS`. Below the search path,
        directories with two-letter language codes are expected, each containing
        a translation file named ``translation.json``. A typical structure looks
        like this::
        
            locales/
                de/
                    translation.json
                fr/
                    translation.json
                it/
                    translation.json
        
        :param lngs: One or more two-letter language codes for the translation 
            files to load, e.g. ``de``.
        :type lngs: str | list[str] | tuple[str]
        :param isForceReload: If `True`, the translation file for a language is
            reloaded if it has been loaded already.
        :type isForceReload: bool
        
        :raise FileNotFoundError: If a translation file for at least one
            language cannot be found.
        :raise json.JSONDecodeError: If at least one translation file does not
            contain a valid JSON document.
        """

        if not isinstance(lngs, list) and not isinstance(lngs, tuple):
            lngs = (lngs,)

        for lng in lngs:
            if lng in self.translationDict and not isForceReload:
                continue

            self.translationPath[lng] = FileHelper.findTranslation('translation.json', lng)

            with open(self.translationPath[lng]) as file:
                translationJsonStr = file.read()

            self.translationDict[lng] = json.loads(translationJsonStr)

    def changeLanguage(self, lng):
        """Changes the target language for translations. Subsequent calls to
        :meth:`~.TranslationManager.translate` will use the new language unless
        overridden temporarily.
        
        :param lng: Two-letter language code for the new language.
        :type lng: str
        """

        self.language = lng

    def translate(
        self,
        key,
        replace,
        lng=None,
        debugOutput=None
    ):
        """Looks up and interpolates a translation for a specific key.
        
        Lazy-loads the translation file for the target language if it has not 
        been loaded yet via :meth:`~.loadLanguages`.
        
        For debugging purposes, messages are written to
        :paramref:`~.TranslationManager.translate.debugOutput` in the following
        cases:
        
        * If the value of a variable cannot be resolved.
        
        :param key: The key to look up.
        :type key: str
        :param replace: The variable values to interpolate. If the variable
            names are numeric indices, a `list` or `tuple` must be provided. If
            the variable names are semantic keys, a `dict` with those keys must
            be provided.
        :type replace: list | tuple | dict
        :param lng: A temporary override for the target language. If `None`,
            :attr:`~.TranslationManager.language` is used.
        :type lng: str | None
        :param debugOutput: A string buffer for appending debug messages. If
            `None`, no messages are produced.
        :type debugOutput: StringIO | None
        
        :raise RuntimeError: If no target language is set.
        :raise FileNotFoundError: If a translation file for the target language
            cannot be found. Only relevant when lazy-loading the translation
            file.
        :raise json.JSONDecodeError: If the translation file does not contain a
            valid JSON document. Only relevant when lazy-loading the translation
            file.
        :raise KeyError: If the key cannot be found in the translation file for
            the target language.
        """
        
        lng = self.language if lng is None else lng
        
        if lng is None:
            raise RuntimeError("No target language set. Call changeLanguage() first.")

        self.loadLanguages(lng)

        try:
            translationPath = self.translationPath[lng]
            translationDict = self.translationDict[lng]
        except:
            raise RuntimeError("Please load a translation file for %s first." % lng)

        if key not in translationDict:
            raise KeyError(
                "Missing key '%s' in translation file %s." % (key, translationPath)
            )

        translation = translationDict[key]

        if 'defaultVariables' in translationDict:
            defaultVariables = translationDict['defaultVariables']
        else:
            defaultVariables = {}

        output = StringIO()
        lastEnd = 0
        for match in TranslationManager.VARIABLE_PATTERN.finditer(translation):
            try:
                # Determine variable ID based on the type of replace.
                if isinstance(replace, list) or isinstance(replace, tuple):
                    varName = int(match.group(1).strip('{}'))
                else:
                    varName = str(match.group(1).strip('{}'))

                # Resolve the actual value of the variable.
                varValue = replace[varName]
            except:
                varValue = None

            # Assume default value if the variable resolved to an empty value.
            if varValue is None or varValue == '':
                try:
                    varValue = defaultVariables[str(varName)]
                except:
                    varValue = '?'
                    if debugOutput is not None:
                        debugOutput.write(
                            "Missing variable %s in message '%s'.\n" % (str(varName), key),
                        )

            output.write(translation[lastEnd:match.start(1)])
            output.write(str(varValue))
            lastEnd = match.end(1)

        output.write(translation[lastEnd:])        
        return output.getvalue()


def createSingleton():
    # Determine locale from environment.
    langkey, encoding = locale.getdefaultlocale()
    match = re.search(r'^([a-z]{2})(?:_|$)', langkey)
    if match:
        return TranslationManager(match.group(1))
    return TranslationManager('en')

Translation = createSingleton()
"""Singleton for translations into the environment's default language."""
