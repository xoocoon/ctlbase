.. currentmodule:: ctlbase.control

control module
==============

.. automodule:: ctlbase.control

.. contents::
   :local:
   :depth: 2

.. _control-concepts:

Core concepts
-------------

The classes in this module provide a range of generic mechanisms supporting the
following concepts.

.. _control-control:

control
^^^^^^^
    
A control encapsulates the logic for "controlling" some other hardware or
software resource. It is implemented by subclassing :class:`~.Control`. Examples
of resources include:
  
* Actuators in a home automation context, optionally coupled to one or more
  sensors.
* Remote file systems to mount and unmount using various credentials from
  desktop keyrings, TPM2 hardware modules and more.
* A mediacenter software exposing an API, e.g. Kodi.

.. _control-action:

action
^^^^^^    

A control defines one or more actions it can perform. Supported actions must be
listed in a custom `Enum` class, called **action Enum**.

As an example, for controlling a movie screen in a home theater, an action 
`Enum` can be implemented as follows::

    class ScreenAction(enum.Enum):
        ROLL_UP = 'roll-up'
        ROLL_DOWN = 'roll-down'

| ... where ``ROLL_UP`` and ``ROLL_DOWN`` are exemplary member names to be used 
  in the Python code.
| ... and ``roll-up`` and ``roll-down`` are the corresponding names to be used
  on the command line when implementing a CLI utility.

Each action `Enum` member must have a single `str` as its value. It must include
an English verb in its infinitive form and must not contain spaces. An object
may be prepended with a hyphen, e.g. ``screen-``. A preposition or similar word,
called addendum, may be appended with or without a hyphen, e.g. ``-down``. An
addendum must be one of the following words: ``on``, ``off``, ``up``, ``right``,
``left``, ``down``, ``out``, ``in``, ``min``, ``max``.

To actually perform an action, :meth:`~.Control.performAction` must be called.

.. _control-mode:

mode
^^^^

A control can optionally support different modes. Each mode can make the control
behave differently for a given action. Supported modes must be listed in a
custom `Enum` class, called **mode Enum**.

As an example, a mode `Enum` can be implemented as follows::

    class ScreenMode(enum.Enum):
        PREVENT_UP = 'prevent-up'
        PREVENT_DOWN = 'prevent-down'

| ... where ``PREVENT_UP`` and ``PREVENT_DOWN`` are examplary member names to be
  used in the Python code.
| ... and ``prevent-up`` and ``prevent-down`` are the corresponding names to be
  used on the command line when implementing a CLI utility.

Each mode `Enum` member must have a single `str` as its value.

In the normal flow, a mode is first set as the **pending mode**. This can be
done by calling :meth:`~.Control.setPendingMode`. It will be unset automatically
if it is not activated within a certain time span. If it is activated in time by
calling :meth:`~.Control.activatePendingMode`, it becomes the **active mode**. 
It will persist until unset with :meth:`~.Control.unsetActiveMode`. Apart from
that, the active mode can be set directly by calling
:meth:`~.Control.setActiveMode`.

It is possible to set one mode as active and another mode as pending at the same
time.

Modes can be retrieved with :meth:`~.Control.getPendingMode` and 
:meth:`~.Control.getActiveMode`.

.. _control-property:

property
^^^^^^^^

By default, a property is read-only. It can be anything worth reporting, e.g.
the state of a sensor, the mount state of a remote file system or a pending mode
or the active mode of the control. If a property is meant to be read-write, 
values can only be changed via an action. Supported properties must be listed in 
a custom `Enum` class, called **property Enum**.

As an example, a property `Enum` can be implemented as follows::

    class ScreenProperty(enum.Enum):
        POSITION = 'position'
        IS_RON = 'relay-on'
        WIDTH = 'width'

| ... where ``POSITION``, ``IS_RON`` and ``WIDTH`` are exemplary member names to
  be used in the Python code.
| ... and ``position``, ``relay-on`` and ``width`` are the corresponding names
  to be used on the command line when implementing a CLI utility.

Each property `Enum` member must have a single `str` as its value.

The current value of a property can be retrieved with
:meth:`~.Control.getProperty`.

.. _control-feedback:

feedback
^^^^^^^^

A control must report feedback from a performed or attempted action, formulated
as a meaningful identifier. While an identifier can be any `str`, common values
are:

* ``already``, if the action has not been performed because the action’s target
  state was already reached.
* ``prevented``, if the action has not been performed due to an active mode or
  any other circumstance preventing the action.
* ``disabled``, if the action has not been performed because it not enabled
  implicitly or is explicitly disabled by configuration.
* ``failed`` if an error occurred while performing the action.
* ``succeeded``, if the action has been performed successfully, which is the
  default if `None` is given.

Classes
-------

.. autoclass:: Control
   :members:
   :private-members: _actionToGrammarEn, _appendMessageFromActionEn, _createExceptionFromActionEn, _performAction, _invalidateCache, _getProperty, _getPropertiesListsEn, _actionToGrammarEn
   :special-members: __init__

.. autoclass:: ControlEnum
   :members:
   :undoc-members:
   :special-members: __eq__

Constants and defaults
----------------------

.. autoclass:: StandardAction
   :members:
   :undoc-members:
   :show-inheritance:

.. autoclass:: StandardMode
   :members:
   :undoc-members:
   :special-members: __eq__
   :show-inheritance:

.. autoclass:: StandardProperty
   :members:
   :undoc-members:
   :show-inheritance:

.. autodata:: ALREADY_OK_SEVERITY

.. autodata:: ALREADY_NOT_OK_SEVERITY

.. autodata:: MODE_DIR_NAME_ENVKEY

.. autodata:: MODE_GROUP_ENVKEY

.. autodata:: MODE_TIMEOUT_DEFAULT_ENVKEY
