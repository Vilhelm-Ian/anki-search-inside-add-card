"""
Python Markdown

A Python implementation of John Gruber's Markdown.

Documentation: https://python-markdown.github.io/
GitHub: https://github.com/Python-Markdown/markdown/
PyPI: https://pypi.org/project/Markdown/

Started by Manfred Stienstra (http://www.dwerg.net/).
Maintained for a few years by Yuri Takhteyev (http://www.freewisdom.org).
Currently maintained by Waylan Limberg (https://github.com/waylan),
Dmitry Shachnev (https://github.com/mitya57) and Isaac Muse (https://github.com/facelessuser).

Copyright 2007-2018 The Python Markdown Project (v. 1.7 and later)
Copyright 2004, 2005, 2006 Yuri Takhteyev (v. 0.2-1.6b)
Copyright 2004 Manfred Stienstra (the original version)

License: BSD (see LICENSE.md for details).
"""

import re
import sys
from collections import namedtuple
from functools import wraps
import warnings
import xml.etree.ElementTree
from .pep562 import Pep562

try:
    from importlib import metadata
except ImportError:
    # <PY38 use backport
    import importlib_metadata as metadata

PY37 = (3, 7) <= sys.version_info
PY312 = (3, 12) <= sys.version_info


# TODO: Remove deprecated variables in a future release.
__deprecated__ = {
    'etree': ('xml.etree.ElementTree', xml.etree.ElementTree),
    'string_type': ('str', str),
    'text_type': ('str', str),
    'int2str': ('chr', chr),
    'iterrange': ('range', range)
}


"""
Constants you might want to modify
-----------------------------------------------------------------------------
"""


BLOCK_LEVEL_ELEMENTS = [
    # Elements which are invalid to wrap in a `<p>` tag.
    # See https://w3c.github.io/html/grouping-content.html#the-p-element
    'address', 'article', 'aside', 'blockquote', 'details', 'div', 'dl',
    'fieldset', 'figcaption', 'figure', 'footer', 'form', 'h1', 'h2', 'h3',
    'h4', 'h5', 'h6', 'header', 'hr', 'main', 'menu', 'nav', 'ol', 'p', 'pre',
    'section', 'table', 'ul',
    # Other elements which Markdown should not be mucking up the contents of.
    'canvas', 'dd', 'dt', 'group', 'iframe', 'li', 'math', 'noscript', 'output',
    'progress', 'script', 'style', 'tbody', 'td', 'th', 'thead', 'tr', 'video'
]

# Placeholders
STX = '\u0002'  # Use STX ("Start of text") for start-of-placeholder
ETX = '\u0003'  # Use ETX ("End of text") for end-of-placeholder
INLINE_PLACEHOLDER_PREFIX = STX+"klzzwxh:"
INLINE_PLACEHOLDER = INLINE_PLACEHOLDER_PREFIX + "%s" + ETX
INLINE_PLACEHOLDER_RE = re.compile(INLINE_PLACEHOLDER % r'([0-9]+)')
AMP_SUBSTITUTE = STX+"amp"+ETX
HTML_PLACEHOLDER = STX + "wzxhzdk:%s" + ETX
HTML_PLACEHOLDER_RE = re.compile(HTML_PLACEHOLDER % r'([0-9]+)')
TAG_PLACEHOLDER = STX + "hzzhzkh:%s" + ETX


"""
Constants you probably do not need to change
-----------------------------------------------------------------------------
"""

# Only load extension entry_points once.
if PY312:
    INSTALLED_EXTENSIONS = metadata.entry_points(group="markdown", name="extensions")
else:
    INSTALLED_EXTENSIONS = metadata.entry_points().get('markdown.extensions', ())
RTL_BIDI_RANGES = (
    ('\u0590', '\u07FF'),
    # Hebrew (0590-05FF), Arabic (0600-06FF),
    # Syriac (0700-074F), Arabic supplement (0750-077F),
    # Thaana (0780-07BF), Nko (07C0-07FF).
    ('\u2D30', '\u2D7F')  # Tifinagh
)


"""
AUXILIARY GLOBAL FUNCTIONS
=============================================================================
"""


def deprecated(message, stacklevel=2):
    """
    Raise a DeprecationWarning when wrapped function/method is called.

    Borrowed from https://stackoverflow.com/a/48632082/866026
    """
    def deprecated_decorator(func):
        @wraps(func)
        def deprecated_func(*args, **kwargs):
            warnings.warn(
                "'{}' is deprecated. {}".format(func.__name__, message),
                category=DeprecationWarning,
                stacklevel=stacklevel
            )
            return func(*args, **kwargs)
        return deprecated_func
    return deprecated_decorator


@deprecated("Use 'Markdown.is_block_level' instead.")
def isBlockLevel(tag):
    """Check if the tag is a block level HTML tag."""
    if isinstance(tag, str):
        return tag.lower().rstrip('/') in BLOCK_LEVEL_ELEMENTS
    # Some ElementTree tags are not strings, so return False.
    return False


def parseBoolValue(value, fail_on_errors=True, preserve_none=False):
    """Parses a string representing bool value. If parsing was successful,
       returns True or False. If preserve_none=True, returns True, False,
       or None. If parsing was not successful, raises  ValueError, or, if
       fail_on_errors=False, returns None."""
    if not isinstance(value, str):
        if preserve_none and value is None:
            return value
        return bool(value)
    elif preserve_none and value.lower() == 'none':
        return None
    elif value.lower() in ('true', 'yes', 'y', 'on', '1'):
        return True
    elif value.lower() in ('false', 'no', 'n', 'off', '0', 'none'):
        return False
    elif fail_on_errors:
        raise ValueError('Cannot parse bool value: %r' % value)


def code_escape(text):
    """Escape code."""
    if "&" in text:
        text = text.replace("&", "&amp;")
    if "<" in text:
        text = text.replace("<", "&lt;")
    if ">" in text:
        text = text.replace(">", "&gt;")
    return text


"""
MISC AUXILIARY CLASSES
=============================================================================
"""


class AtomicString(str):
    """A string which should not be further processed."""
    pass


class Processor:
    def __init__(self, md=None):
        self.md = md

    @property
    @deprecated("Use 'md' instead.")
    def markdown(self):
        # TODO: remove this later
        return self.md


class HtmlStash:
    """
    This class is used for stashing HTML objects that we extract
    in the beginning and replace with place-holders.
    """

    def __init__(self):
        """ Create a HtmlStash. """
        self.html_counter = 0  # for counting inline html segments
        self.rawHtmlBlocks = []
        self.tag_counter = 0
        self.tag_data = []  # list of dictionaries in the order tags appear

    def store(self, html):
        """
        Saves an HTML segment for later reinsertion.  Returns a
        placeholder string that needs to be inserted into the
        document.

        Keyword arguments:

        * html: an html segment

        Returns : a placeholder string

        """
        self.rawHtmlBlocks.append(html)
        placeholder = self.get_placeholder(self.html_counter)
        self.html_counter += 1
        return placeholder

    def reset(self):
        self.html_counter = 0
        self.rawHtmlBlocks = []

    def get_placeholder(self, key):
        return HTML_PLACEHOLDER % key

    def store_tag(self, tag, attrs, left_index, right_index):
        """Store tag data and return a placeholder."""
        self.tag_data.append({'tag': tag, 'attrs': attrs,
                              'left_index': left_index,
                              'right_index': right_index})
        placeholder = TAG_PLACEHOLDER % str(self.tag_counter)
        self.tag_counter += 1  # equal to the tag's index in self.tag_data
        return placeholder


# Used internally by `Registry` for each item in its sorted list.
# Provides an easier to read API when editing the code later.
# For example, `item.name` is more clear than `item[0]`.
_PriorityItem = namedtuple('PriorityItem', ['name', 'priority'])


class Registry:
    """
    A priority sorted registry.

    A `Registry` instance provides two public methods to alter the data of the
    registry: `register` and `deregister`. Use `register` to add items and
    `deregister` to remove items. See each method for specifics.

    When registering an item, a "name" and a "priority" must be provided. All
    items are automatically sorted by "priority" from highest to lowest. The
    "name" is used to remove ("deregister") and get items.

    A `Registry` instance it like a list (which maintains order) when reading
    data. You may iterate over the items, get an item and get a count (length)
    of all items. You may also check that the registry contains an item.

    When getting an item you may use either the index of the item or the
    string-based "name". For example:

        registry = Registry()
        registry.register(SomeItem(), 'itemname', 20)
        # Get the item by index
        item = registry[0]
        # Get the item by name
        item = registry['itemname']

    When checking that the registry contains an item, you may use either the
    string-based "name", or a reference to the actual item. For example:

        someitem = SomeItem()
        registry.register(someitem, 'itemname', 20)
        # Contains the name
        assert 'itemname' in registry
        # Contains the item instance
        assert someitem in registry

    The method `get_index_for_name` is also available to obtain the index of
    an item using that item's assigned "name".
    """

    def __init__(self):
        self._data = {}
        self._priority = []
        self._is_sorted = False

    def __contains__(self, item):
        if isinstance(item, str):
            # Check if an item exists by this name.
            return item in self._data.keys()
        # Check if this instance exists.
        return item in self._data.values()

    def __iter__(self):
        self._sort()
        return iter([self._data[k] for k, p in self._priority])

    def __getitem__(self, key):
        self._sort()
        if isinstance(key, slice):
            data = Registry()
            for k, p in self._priority[key]:
                data.register(self._data[k], k, p)
            return data
        if isinstance(key, int):
            return self._data[self._priority[key].name]
        return self._data[key]

    def __len__(self):
        return len(self._priority)

    def __repr__(self):
        return '<{}({})>'.format(self.__class__.__name__, list(self))

    def get_index_for_name(self, name):
        """
        Return the index of the given name.
        """
        if name in self:
            self._sort()
            return self._priority.index(
                [x for x in self._priority if x.name == name][0]
            )
        raise ValueError('No item named "{}" exists.'.format(name))

    def register(self, item, name, priority):
        """
        Add an item to the registry with the given name and priority.

        Parameters:

        * `item`: The item being registered.
        * `name`: A string used to reference the item.
        * `priority`: An integer or float used to sort against all items.

        If an item is registered with a "name" which already exists, the
        existing item is replaced with the new item. Tread carefully as the
        old item is lost with no way to recover it. The new item will be
        sorted according to its priority and will **not** retain the position
        of the old item.
        """
        if name in self:
            # Remove existing item of same name first
            self.deregister(name)
        self._is_sorted = False
        self._data[name] = item
        self._priority.append(_PriorityItem(name, priority))

    def deregister(self, name, strict=True):
        """
        Remove an item from the registry.

        Set `strict=False` to fail silently.
        """
        try:
            index = self.get_index_for_name(name)
            del self._priority[index]
            del self._data[name]
        except ValueError:
            if strict:
                raise

    def _sort(self):
        """
        Sort the registry by priority from highest to lowest.

        This method is called internally and should never be explicitly called.
        """
        if not self._is_sorted:
            self._priority.sort(key=lambda item: item.priority, reverse=True)
            self._is_sorted = True

    # Deprecated Methods which provide a smooth transition from OrderedDict

    def __setitem__(self, key, value):
        """ Register item with priorty 5 less than lowest existing priority. """
        if isinstance(key, str):
            warnings.warn(
                'Using setitem to register a processor or pattern is deprecated. '
                'Use the `register` method instead.',
                DeprecationWarning,
                stacklevel=2,
            )
            if key in self:
                # Key already exists, replace without altering priority
                self._data[key] = value
                return
            if len(self) == 0:
                # This is the first item. Set priority to 50.
                priority = 50
            else:
                self._sort()
                priority = self._priority[-1].priority - 5
            self.register(value, key, priority)
        else:
            raise TypeError

    def __delitem__(self, key):
        """ Deregister an item by name. """
        if key in self:
            self.deregister(key)
            warnings.warn(
                'Using del to remove a processor or pattern is deprecated. '
                'Use the `deregister` method instead.',
                DeprecationWarning,
                stacklevel=2,
            )
        else:
            raise KeyError('Cannot delete key {}, not registered.'.format(key))

    def add(self, key, value, location):
        """ Register a key by location. """
        if len(self) == 0:
            # This is the first item. Set priority to 50.
            priority = 50
        elif location == '_begin':
            self._sort()
            # Set priority 5 greater than highest existing priority
            priority = self._priority[0].priority + 5
        elif location == '_end':
            self._sort()
            # Set priority 5 less than lowest existing priority
            priority = self._priority[-1].priority - 5
        elif location.startswith('<') or location.startswith('>'):
            # Set priority halfway between existing priorities.
            i = self.get_index_for_name(location[1:])
            if location.startswith('<'):
                after = self._priority[i].priority
                if i > 0:
                    before = self._priority[i-1].priority
                else:
                    # Location is first item`
                    before = after + 10
            else:
                # location.startswith('>')
                before = self._priority[i].priority
                if i < len(self) - 1:
                    after = self._priority[i+1].priority
                else:
                    # location is last item
                    after = before - 10
            priority = before - ((before - after) / 2)
        else:
            raise ValueError('Not a valid location: "%s". Location key '
                             'must start with a ">" or "<".' % location)
        self.register(value, key, priority)
        warnings.warn(
            'Using the add method to register a processor or pattern is deprecated. '
            'Use the `register` method instead.',
            DeprecationWarning,
            stacklevel=2,
        )


def __getattr__(name):
    """Get attribute."""

    deprecated = __deprecated__.get(name)
    if deprecated:
        warnings.warn(
            "'{}' is deprecated. Use '{}' instead.".format(name, deprecated[0]),
            category=DeprecationWarning,
            stacklevel=(3 if PY37 else 4)
        )
        return deprecated[1]
    raise AttributeError("module '{}' has no attribute '{}'".format(__name__, name))


if not PY37:
    Pep562(__name__)
