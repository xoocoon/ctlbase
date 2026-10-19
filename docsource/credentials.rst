.. currentmodule:: ctlbase.credentials

credentials module
==================

.. automodule:: ctlbase.credentials

.. contents::
   :local:
   :depth: 2

Classes
-------

.. autoclass:: CredentialReader
   :members:
   :special-members: __init__
   :exclude-members: TypeToArgs
   :show-inheritance:

.. autoclass:: KeyringReader
   :members:
   :special-members: __init__
   :show-inheritance:

.. autoclass:: Tpm2Reader
   :members:
   :special-members: __init__
   :show-inheritance:

.. autoclass:: FileReader
   :members:
   :special-members: __init__
   :show-inheritance:

.. autoclass:: PromptReader
   :members:
   :special-members: __init__
   :show-inheritance:

.. autoclass:: BaseReader
   :members:
   :exclude-members: credentialKeysOtherThanLogin
   :private-members: _readCredential
   :special-members: __init__

.. autoclass:: TemporaryCredentials
   :members:
   :special-members: __init__

Constants and defaults
----------------------

.. autoclass:: CredentialKey
   :members:
   :undoc-members:

.. autodata:: CREDENTIALS_DIRECTORY_DEFAULT

