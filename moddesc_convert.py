"""convert ME3 Tweaks moddesc.ini file into json and back"""

from abc import ABC, abstractmethod
from collections.abc import Iterable, MutableSequence
from dataclasses import dataclass, field, fields
from difflib import unified_diff
from multiprocessing import Value
from pathlib import Path
from rich.console import Console
from rich.pretty import Pretty
from rich.syntax import Syntax
from typing import Any, Generic, Self, TypeAlias, TypeVar, cast, overload, override
import click
import codecs
import json
import re
import sys

_JSON_INDENT: int = 4
JsonValue: TypeAlias = str | dict[str, "JsonValue"] | list["JsonValue"] | None

class IdKeyStore():
    _id_none: int = -1
    _id: dict[int, str] = {}
    _key_none: str = 'None'
    _key: dict[str, int] = {}

    def __repr__(self) -> str:
        return f'{self.__class__.__name__}(_id={self._id}, _key={self._key})'

    def set_id(self, id: int, key: str) -> None:
        if id <= self._id_none:
            raise ValueError(f"Id for key '{key}' cannot be lower or equal than ({self._id_none}).")
        if key == self._key_none:
            raise ValueError(f"Cannot set key '{self._key_none}' for id ({id}).")
        self._id[id] = key
        self._key[key] = id
    def set_key(self, key: str, id: int) -> None:
        self.set_id(id, key)
    def get_id(self, key: str) -> int:
        if key in self._key.keys():
            return self._key[key]
        return self._id_none
    def get_key(self, id: int) -> str:
        if id in self._id.keys():
            return self._id[id]
        return self._key_none
multilist_key_map: IdKeyStore = IdKeyStore()

@dataclass
class IFormatting(ABC):
    uses_key: bool = field(default=True)
    key: str = field(default="")

    def __post_init__(self) -> None:
        # set default keys
        for name, attr in self.serializable_fields().items():
            if attr.key:
                continue
            attr.key = name

    def get_id(self) -> str:
        """Returns main key name that this object can be represented as."""
        return f"{self.__class__.__name__}[{id(self)}]"
    @classmethod
    def get_type(cls) -> type["IFormatting"]:
        """Get reference to class type"""
        return cls

    def serializable_field_names(self) -> set[str]:
        """List of names available for serialization to supported formats."""
        names: set[str] = set()
        ignored: list[str] = [
            'uses_key',
            'ini_separator',
            'wrapper_left',
            'wrapper_right',
            'quote_wrap'
        ]
        for f in fields(self):
            if f.name in ignored:
                continue
            names.add(f.name)
        return names
    def serializable_fields(self) -> dict[str, IFormatting]:
        """List of fields available for serialization to supported formats.\n
            Uses serializable_field_names() to determine which files to return."""
        attrs: dict[str, IFormatting] = {}
        # do not iterate over serializable_field_names()
        # its a set and the loop gets wonky and unordered
        for f in fields(self):
            if f.name not in self.serializable_field_names():
                continue
            attr: IFormatting | None = getattr(self, f.name, None)
            if isinstance(attr, IFormatting):
                attrs[f.name] = attr
        return attrs
    def get_field(self, name: str) -> IFormatting | None:
        """Get classs field, must exist in serializable names."""
        if name in self.serializable_field_names() and hasattr(self, name):
            return cast(IFormatting, getattr(self, name))
        return None
    def set_field(self, name: str, value: IFormatting) -> None:
        """Set value of a class field, must exist in serializable names."""
        if name in self.serializable_field_names() and hasattr(self, name):
            setattr(self, name, value)
    @abstractmethod
    def is_empty(self) -> bool:
        """Return true if this Value holds no data."""
        raise NotImplementedError()
    @abstractmethod
    def reset_value(self) -> None:
        """Reset value to it's defaults."""
        raise NotImplementedError()

    @abstractmethod
    def as_ini(self, formatted: bool = True, pretty: bool = False) -> str:
        """Return value representation as INI moddesc format."""
        raise NotImplementedError()
    @abstractmethod
    def set_from_ini(self, ini: str | list[str]) -> Self:
        """Parse value from INI moddesc format."""
        raise NotImplementedError()

    @abstractmethod
    def jsonable(self) -> JsonValue:
        """Return Value representation as combination of python dicts and lists."""
        raise NotImplementedError()
    def as_json(self) -> str:
        """Return Value representation as JSON moddesc format."""
        return json.dumps(obj=self.jsonable(), indent=_JSON_INDENT)
    @abstractmethod
    def set_from_json(self, json_data: JsonValue) -> Self:
        """Parse value from JSON moddesc format."""
        raise NotImplementedError()
TFormatting = TypeVar(name="TFormatting", bound=IFormatting)

class IJsonableDict(IFormatting, ABC):
    @override
    @abstractmethod
    def jsonable(self) -> dict[str, JsonValue]:
        raise NotImplementedError()

# =============================================================================
#
# VALUE TYPES
#   - RawValue
#   - BoolValue (RawValue)
#   - IntValue (RawValue)
#   - EnumValue (RawValue)
#   - PathValue (RawValue)
#   - UrlValue (RawValue)
#   - IWrappedValue
#   - OptionsValue (IWrappedValue)
#   - DlcOptionKey (OptionsValue)
#   - DlcOptions (OptionsValue)
#   - DlcValue (RawValue)
#   - ListValue
#   - ArrayValue(ListValue)
#   - DictValue(ListValue)
#   - MultiListValue(DictValue)
#
# =============================================================================

@dataclass
class RawValue(IFormatting):
    """Holds one specific value that will be represented as raw value."""
    value: str = field(default="")

    @override
    def is_empty(self) -> bool:
        return self.value == ''
    @override
    def reset_value(self) -> None:
        self.value = ''

    @override
    def as_ini(self, formatted: bool = True, pretty: bool = False) -> str:
        if self.is_empty():
            return ""
        # return valid ini pattern as key = value
        # formatted bool controls if to include "pretty" formatting or not
        keyed: str = (f'{self.key} = ' if formatted else f'{self.key}=') if self.uses_key else ''
        return f'{keyed}{self.value}'
    @override
    def set_from_ini(self, ini: str | list[str]) -> Self:
        if isinstance(ini, list):
            raise TypeError(f'{self.__class__.__name__} cannot accept INI input from list variable.')

        # ini pattern is simple, key = value
        self.reset_value()
        ini = ini.strip()

        if not self.uses_key:
            self.key = ini
            self.value = ini
            return self

        kvp: list[str] = re.split(pattern=r'\s*=\s*', string=ini, maxsplit=1)
        key: str = ''
        value: str = ''
        if len(kvp) == 2:
            key = kvp[0].strip()
            value = kvp[1].strip()
        if not key or not value:
            raise ValueError(f"Could not parse key value pair from provided ini line.\n\nini: {ini}\nkey: {key}\nvalue: {value}")
        self.key: str = key
        self.value = value
        return self

    @override
    def jsonable(self) -> JsonValue:
        return self.value if not self.is_empty() else ""
    @override
    def set_from_json(self, json_data: JsonValue) -> Self:
        self.reset_value()
        if not self.uses_key and isinstance(json_data, str):
            self.key = json_data
            self.value = json_data
            return self

        if not isinstance(json_data, dict):
            return self # not a dict, cant tell whats key and whats value
        if len(json_data) != 1:
            return self # theres more than just key => value

        key, value = next(iter(json_data.items()))
        value = cast(str, value)
        if not key or not value:
            raise ValueError(f"Could not parse key value pair from provided json data.\n\njson: {json_data}\nkey: {key}\nvalue: {value}")
        self.key = key
        self.value = value
        return self
TValue = TypeVar(name="TValue", bound=RawValue)

@dataclass
class BoolValue(RawValue):
    """Holds one specific value that will be represented as a boolean value."""
    @override
    def set_from_ini(self, ini: str | list[str]) -> Self:
        if isinstance(ini, list):
            raise TypeError(f'{self.__class__.__name__} cannot accept INI input from list variable.')
        _ = super().set_from_ini(ini)
        if self.value.lower() not in ['true','false']:
            raise ValueError(f"BoolValue can only accept 'true' or 'false' values.\n\nref: {self.value}")
            #self.reset_value()
        return self

@dataclass
class IntValue(RawValue):
    """Holds one specific value holding close encounters of the numeric kind"""
    @override
    def set_from_ini(self, ini: str | list[str]) -> Self:
        if isinstance(ini, list):
            raise TypeError(f'{self.__class__.__name__} cannot accept INI input from list variable.')
        _ = super().set_from_ini(ini)
        try:
            _ = int(self.value)
        except ValueError:
            raise ValueError(f"IntValue can accept only numbers as its value.\n\nref: {self.value}")
        _ = super().set_from_ini(ini)
        return self

@dataclass
class EnumValue(RawValue):
    """Holds one specific value from list of possible values."""
    items: set[str] = field(default_factory=set[str])
    @override
    def set_from_ini(self, ini: str | list[str]) -> Self:
        if isinstance(ini, list):
            raise TypeError(f'{self.__class__.__name__} cannot accept INI input from list variable.')
        _ = super().set_from_ini(ini)
        if self.value not in self.items:
            raise ValueError(f"EnumValue can accept only one of its pre-defined values [{",".join(self.items)}].\n\nref: {self.value}")
            #self.reset_value()
        return self

@dataclass
class PathValue(RawValue):
    """Holds one specific value that will be represented as a path, eg. \\Directory\\File.jpeg."""
    pass

@dataclass
class UrlValue(RawValue):
    """Holds one specific value that will be represented as an url, eg. https://masseffect.com."""
    pass

@dataclass
class IWrappedValue(RawValue, ABC):
    """Interface for value thats wrapped in characters, for example as key = (value)"""
    wrapper_left: str = field(default="", repr=False)
    wrapper_right: str = field(default="", repr=False)

    @override
    def as_ini(self, formatted: bool = True, pretty: bool = False) -> str:
        if self.is_empty():
            return ''
        keyed: str = (f'{self.key} = ' if formatted else f'{self.key}=') if self.uses_key else ''
        return f'{keyed}{self.wrapper_left}{self.value}{self.wrapper_right}'
    @override
    def set_from_ini(self, ini: str | list[str]) -> Self:
        _ = super().set_from_ini(ini)
        if self.is_empty():
            return self

        # confirm that the value was wrapped in correct wrapper
        value: str = self.value
        if value.startswith(self.wrapper_left) and value.endswith(self.wrapper_right):
            value = value[1:-1]
            self.value: str = value
        return self

@dataclass
class StringValue(IWrappedValue, RawValue):
    """Holds one specific value that will be represented as a string value with single quotes."""
    wrapper_left: str = field(default="'", repr=False)
    wrapper_right: str = field(default="'", repr=False)

@dataclass
class TextValue(IWrappedValue, RawValue):
    """Holds one specific value that will be represented as a string value with double quotes."""
    wrapper_left: str = field(default='"', repr=False)
    wrapper_right: str = field(default='"', repr=False)

@dataclass
class TagValue(IWrappedValue, RawValue):
    """Special value type thats used only for tagging the main dictionaries in moddesc format."""
    uses_key: bool = field(default=False)
    wrapper_left: str = field(default="[", repr=False)
    wrapper_right: str = field(default="]", repr=False)

@dataclass
class OptionsValue(IJsonableDict, IWrappedValue, RawValue):
    """Holds arbitrary amount of values, each with their own type."""
    ini_separator: str = field(default=",", repr=False)
    wrapper_left: str = field(default="[", repr=False)
    wrapper_right: str = field(default="]", repr=False)

    # INI option values are separated by a comma and wrapped in [] brackets
    @override
    def as_ini(self, formatted: bool = True, pretty: bool = False) -> str:
        if self.is_empty():
            return ''

        parts: list[str] = []
        for attr in self.serializable_fields().values():
            if not attr.is_empty():
                parts.append(attr.as_ini(formatted))
        return f'{self.key}={self.wrapper_left}{self.ini_separator.join(parts)}{self.wrapper_right}'
    # INI option values are separated by a comma and wrapped in [] brackets
    @override
    def set_from_ini(self, ini: str | list[str]) -> Self:
        _ = super().set_from_ini(ini)

        # match only separators outside of [] for recursion
        recursive_options: list[str] = re.split(rf'{self.ini_separator}(?=(?:[^\[\]]*\[[^\[\]]*\])*[^\[\]]*$)', self.value)

        for option in recursive_options:
            # parse option as RawValue
            kvp: RawValue = RawValue().set_from_ini(option)
            if not kvp.is_empty():
                # get attr by key
                attr = self.get_field(kvp.key)
                if isinstance(attr, IFormatting):
                    _ = attr.set_from_ini(option)
        return self

    @override
    def jsonable(self) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {}
        for name, attr in self.serializable_fields().items():
            if attr.is_empty():
                continue
            result[name] = attr.jsonable()
        return result
    @override
    def set_from_json(self, json_data: JsonValue) -> Self:
        if not isinstance(json_data, dict):
            return self
        # similar to dicts, the self.key value should equal the key of received data:
        if not json_data[self.key]:
            return self
        json_data = cast(dict[str, JsonValue], json_data[self.key])
        if self.is_empty():
            self.value: str = str(json_data)

        for key, value in json_data.items():
            attr = self.get_field(key)
            if isinstance(attr, IFormatting):
                _ = attr.set_from_json({key: value})
        return self

@dataclass
class DlcOptionKey(OptionsValue):
    """OptionKey options for DLC values."""
    option: RawValue = field(default_factory=RawValue)
    uistring: StringValue = field(default_factory=StringValue)

@dataclass
class DlcOptions(OptionsValue):
    """DLC options for DLC values."""
    minversion: RawValue = field(default_factory=RawValue)
    optionkey: DlcOptionKey = field(default_factory=DlcOptionKey)

@dataclass
class DlcValue(IJsonableDict, RawValue):
    """Option values reside in [] bloc attached to RawValue,
    its content is accessible in the .options property as parsed formatting object."""
    quote_wrap: bool = field(default=False)
    options: DlcOptions = field(default_factory=DlcOptions)

    # NOTE: I'm setting options= into the ini value and then removing it on as_ini
    # this is because both RawValue and OptionsValue expect key=value input
    # and initial options [tag] dont have equalsign so its attached there to correctly
    # recognize options as options, the key of the object also becomes 'options'

    # DLC values are just RawValues with extra DLC options.
    @override
    def as_ini(self, formatted: bool = True, pretty: bool = False) -> str:
        # return with quotes if uistring is not empty
        # this is only for CUSTOMDLC fields
        result: str = self.key + self.options.as_ini(False)
        result = result.replace('options=', '', 1)
        if self.quote_wrap and not self.options.optionkey.uistring.is_empty():
            result = f'"{result}"'
        return result
    @override
    def set_from_ini(self, ini: str | list[str]) -> Self:
        if isinstance(ini, list):
            raise TypeError(f'{self.__class__.__name__} cannot accept INI input from list variable.')
        _ = self.reset_value()

        # DLC format example: 
        # NOTE: can be wrapped in quotes if uistring option exists
        # +DLC_MOD_EGM[minversion=1.0.6,optionkey=[option=+D0DB2884,uistring='Squadmate Pack (Full)']]

        ini = ini.strip()
        # wrapped in quotes?
        if ini.startswith('"') and ini.endswith('"'):
            ini = ini[1:-1]
        # is there options?
        if self.options.wrapper_left in ini and ini.endswith(self.options.wrapper_right):
            value, options = ini.split(self.options.wrapper_left, 1)
            # put the separator back after it got splitted away
            options: str = f'{self.options.wrapper_left}{options}'
            _ = super().set_from_ini(f'{value}{options}')
            self.key: str = value
            _ = self.options.set_from_ini(f'options={options}')
        # no options exist
        # treat this as plain RawValue and just call the ini parsing on super()
        else:
            _ = super().set_from_ini(ini)
        return self

    @override
    def jsonable(self) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {}
        result[self.key] = self.options.jsonable()
        return result
    @override
    def set_from_json(self, json_data: JsonValue) -> Self:
        self.reset_value()
        if not isinstance(json_data, dict):
            return self

        for key, value in json_data.items():
            self.key = key
            self.value: str = str(value)
            _ = self.options.set_from_json({'options': value})
        return self

@dataclass
class MultilistIDValue(RawValue):
    """Holds either ID or friendly_key value"""

    @override
    def as_ini(self, formatted: bool = True, pretty: bool = False) -> str:
        new_value: RawValue = RawValue()
        new_value.uses_key = self.uses_key
        new_value.key = self.key if new_value.uses_key else self.value_as_id()
        new_value.value = self.value_as_id()
        return new_value.as_ini(formatted)
    @override
    def jsonable(self) -> JsonValue:
        new_value: RawValue = RawValue()
        new_value.uses_key = self.uses_key
        new_value.key = self.key if new_value.uses_key else self.value_as_key()
        new_value.value = self.value_as_key()
        return new_value.jsonable()

    def value_as_key(self) -> str:
        if self.value.isdigit():
            friendly_key: str = multilist_key_map.get_key(int(self.value))
            if friendly_key != multilist_key_map._key_none:  # pyright: ignore[reportPrivateUsage]
                return friendly_key
        return self.value
    def value_as_id(self) -> str:
        if not self.value.isdigit():
            id: int = multilist_key_map.get_id(self.value)
            if id != multilist_key_map._id_none:  # pyright: ignore[reportPrivateUsage]
                return str(id)
        return self.value

@dataclass
class ListValue(RawValue, MutableSequence[TFormatting], Generic[TFormatting]):
    ini_separator: str = field(default=";", repr=False)
    item_type: type[TFormatting] | type[None] = type(None)
    items: list[TFormatting] = field(default_factory=list[TFormatting])

    @override
    def __init__(self, item_type: type[TFormatting]) -> None:
        super().__init__()
        self.item_type = item_type
        self.items = []
        return

    # MutableSequence[IFormatting]
    @override
    def __len__(self) -> int:
        return len(self.items)

    @overload
    def __getitem__(self, index: int) -> TFormatting: ...
    @overload
    def __getitem__(self, index: slice) -> list[TFormatting]: ...
    @override
    def __getitem__(self, index: int | slice) -> IFormatting | list[TFormatting]:
        return self.items[index]

    @overload
    def __setitem__(self, index: int, value: TFormatting) -> None: ...
    @overload
    def __setitem__(self, index: slice, value: Iterable[TFormatting]) -> None: ...
    @override
    def __setitem__(self, index: int | slice, value: TFormatting | Iterable[TFormatting]) -> None:
        if isinstance(index, slice):
            if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
                self.items[index] = list[TFormatting](value)
                return
            raise TypeError(f"Slice assignment requires an iterable of IFormatting items.\n\nref: {value}")
        if isinstance(value, IFormatting):
            self.items[index] = value
            return
        raise TypeError("IFormattingList only accepts IFormatting items.")

    @overload
    def __delitem__(self, index: int) -> None: ...
    @overload
    def __delitem__(self, index: slice) -> None: ...
    @override
    def __delitem__(self, index: int | slice) -> None:
        del self.items[index]

    @override
    def insert(self, index: int, value: TFormatting) -> None:
        self.items.insert(index, value)
    @override
    def is_empty(self) -> bool:
        return len(self.items) <= 0
    def reset_items(self) -> Self:
        self.items = []
        return self

    def get_index_by_key(self, key: str) -> int:
        """returns -1 if not found"""
        return next((i for i, item in enumerate(self.items) if item.key == key), -1)
    def get_item_by_key(self, key: str) -> TFormatting | None:
        if self.get_index_by_key(key) < 0:
            return None
        return self.items[self.get_index_by_key(key)]

    @override
    def as_ini(self, formatted: bool = True, pretty: bool = False) -> str:
        # pretty multilists
        if self.key.startswith('multilist') and pretty:
            keyed: str = f'{self.key} = '
            spaced: str = ' ' * len(keyed)
            items: list[str] = []
            for item in self.items:
                items.append(spaced + item.as_ini(pretty=pretty))
            return f'{keyed}{f'{self.ini_separator}\n'.join(items).strip()}'

        # ugly
        if self.is_empty():
            return ""
        # save ini into the value param
        self.value: str = self.ini_separator.join(item.as_ini(formatted) for item in self.items).strip()
        # return RawValue as usual
        return super().as_ini(formatted)

    @override
    def set_from_ini(self, ini: str | list[str]) -> Self:
        # cannot map raw value into items
        if self.item_type == type(None):
            return self
        _ = self.reset_items()

        items: list[str] = []
        if isinstance(ini, list):
            # setting from list does not allow me to know a key
            self.key: str = f'list[{len(ini)}]'
            items = ini
        else:
            # first parse the key and raw value into key and value fields
            _ = super().set_from_ini(ini)
            items = self.value.split(self.ini_separator)

        for item in items:
            # item_type() cannot be None here
            # create new item from item_type
            # turn off key=value parsing
            new_item: TFormatting = cast(TFormatting, self.item_type())
            new_item.uses_key = False
            self.items.append(new_item.set_from_ini(item))
        return self

    @override
    def jsonable(self) -> JsonValue:
        # collapse key value dict jsonable content
        if issubclass(self.item_type, IJsonableDict):
            result_dict: dict[str, JsonValue] = {}
            for item in self.items:
                item = cast(IJsonableDict, item)
                if not item or item.is_empty():
                    continue
                result_dict.update(item.jsonable())
            return result_dict

        # return as list of values
        result: list[JsonValue] = []
        for item in self.items:
            if item.is_empty():
                continue
            result.append(item.jsonable())
        return result
    @override
    def set_from_json(self, json_data: JsonValue) -> Self:
        _ = self.reset_items()
        # convert string to data if needed
        json_obj: JsonValue = cast(JsonValue, json.loads(json_data)) if isinstance(json_data, str) else json_data
        if not json_obj:
            return self
        # flatten json keys for those that are the same name of this attr:
        if isinstance(json_obj, dict) and self.key in json_obj.keys():
            json_obj = cast(dict[str, JsonValue], json_obj[self.key])

        # dictionaries?
        if isinstance(json_obj, dict):
            for key, item in json_obj.items():
                new_item = cast(TFormatting, self.item_type())
                _ = new_item.set_from_json({key: item})
                if not new_item.is_empty():
                    self.items.append(new_item)
        # lists?
        elif isinstance(json_obj, list):
            for item in json_obj:
                new_item = cast(TFormatting, self.item_type())
                new_item.uses_key = False
                _ = new_item.set_from_json(item)
                if not new_item.is_empty():
                    self.items.append(new_item)
        return self

@dataclass
class ArrayValue(ListValue[TFormatting]):
    ini_separator: str = field(default=',', repr=False)
    item_type: type[TFormatting] | type[None] = type(None)
    items: list[TFormatting] = field(default_factory=list[TFormatting])

    @override
    def __init__(self, item_type: type[TFormatting]) -> None:
        super().__init__(item_type)
        self.item_type = item_type
        self.items = []
        return

    @override
    def as_ini(self, formatted: bool = True, pretty: bool = False) -> str:
        # return wrapped in brackets as ((item),(item),(item))
        if not pretty:
            keyed: str = f'{self.key} = ' if formatted else f'{self.key}='
            items: list[str] = []
            for item in self.items:
                items.append(f'({item.as_ini(formatted)})')
            return f"{keyed}({self.ini_separator.join(items)})"

        # pretty output of array so its not all dumped on single line
        # useless as mod manager doesnt like reading this, but good for console echos
        lines: list[str] = []
        lines.append(f'{self.key} = (')
        for item in self.items:
            inner: list[str] = []
            inner.append('    (')
            inner.append(item.as_ini(pretty=pretty))
            inner.append(f'    ){self.ini_separator if not item == self.items[-1] else ''}')
            lines.append(f'\n'.join(inner))
        lines.append(')')
        return '\n'.join(lines)
    @override
    def set_from_ini(self, ini: str | list[str]) -> Self:
        if isinstance(ini, list):
            raise TypeError(f'{self.__class__.__name__} cannot accept INI input from list variable.')

        # cannot map raw value into items
        if self.item_type == type(None):
            return self
        _ = self.reset_items()

        # try parse
        # this will separate key=value
        raw = RawValue().set_from_ini(ini)
        if raw.is_empty():
            return self
        self.key: str = raw.key
        ini = raw.value

        # format: ((item),(item),(item))
        # get rid of wrapping brackets
        if not ini.startswith('(') or not ini.endswith(')'):
            raise ValueError(f"Invalid format, INI line containing array of items must begin with ( and end with ).\n\nref: {ini}")
        ini = ini[1:-1].strip()

        # format: (item),(item),(item)
        # getting rid of the brackets again from start and end
        if not ini.startswith('(') or not ini.endswith(')'):
            raise ValueError(f"Invalid format, INI array item(s) not wrapped inside ( ) brackets correctly.\n\nref: {ini}")
        ini = ini[1:-1].strip()

        # format: item),(item),(item
        # now I can just split by bracketed comma and get the separate items
        # matching ),( with possible spaces between, idk if there could be but rather make sure
        items: list[str] = re.split(rf'\)\s*?{self.ini_separator}\s*?\(', ini)

        # since arrays are packed to single line, lets unpack em
        # create pattern out of accepted fields and split the string by them:
        # r',\s*?(?=Description\s*?=|OptionKey\s*?=|DependsOnKeys\s*?=)'
        keywords: set[str] = cast(TFormatting, self.item_type()).serializable_field_names()
        pattern: str = rf'{self.ini_separator}\s*?(?=' + r'|'.join(re.escape(keyword) + r'\s*?=' for keyword in keywords) + r')'
        for i, item in enumerate[str](items):
            # then pull the separated into a list
            new_item = cast(TFormatting, self.item_type()).set_from_ini(re.split(pattern, item))
            if new_item.is_empty():
                continue
            self.items.append(new_item)
        return self

@dataclass
class DictValue(ListValue[TValue]):
    ini_separator: str = field(default='\n', repr=False)
    item_type: type[TValue] | type[None] = type(None)
    items: list[TValue] = field(default_factory=list[TValue])

    @override
    def __init__(self, item_type: type[TValue]) -> None:
        super().__init__(item_type)
        self.item_type = item_type
        self.items = []
        return

    @override
    def as_ini(self, formatted: bool = True, pretty: bool = False) -> str:
        # return as key = value
        items: list[str] = []
        for item in self.items:
            items.append(item.as_ini(formatted))
        return self.ini_separator.join(items)
    @override
    def set_from_ini(self, ini: str | list[str]) -> Self:
        # cannot map raw value into items
        if self.item_type == type(None):
            return self
        _ = self.reset_items()

        # parse ini input and append to items
        lines: list[str] = ini if isinstance(ini, list) else ini.split(self.ini_separator)
        for line in lines:
            if not is_valid_ini_line(line):
                continue
            new_item = self.item_type()
            if isinstance(new_item, IFormatting):
                new_item = new_item.set_from_ini(line)
                if new_item.is_empty():
                    continue
                self.items.append(new_item)
        return self

    @override
    def jsonable(self) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {}
        for item in self.items:
            if item.is_empty():
                continue
            if isinstance(item, ListValue):
                result[item.key] = item.jsonable()
            else:
                result[item.key] = item.value
        return result

@dataclass
class MultiListValue(ListValue[PathValue]):
    friendly_key: str = field(default='')
    comment: str = field(default='')
    item_type: type[PathValue] | type[None] = PathValue
    items: list[PathValue] = field(default_factory=list)

    @override
    def __init__(self) -> None:
        super().__init__(PathValue)
        self.items = []

    @override
    def as_ini(self, formatted: bool = True, pretty: bool = False) -> str:
        # return as key = value
        items: list[str] = []
        for item in self.items:
            # kinda hacky but the RawValue list format returns the items as 1 = files, 2 = files, etc
            result = item.as_ini(formatted)
            items.append(result)
        line_key = f'{self.key} = ' if formatted else f'{self.key}='
        line_items: str = line_key + self.ini_separator.join(items)
        line_data: str = ''
        if self.friendly_key:
            line_key = '; ' + line_key if formatted else ';' + line_key
            line_friendly: str = f'{self.friendly_key}'
            line_comment: str = ''
            if self.comment:
                line_comment = f' ; {self.comment}' if formatted else f';{self.comment}'
            line_data = f'{line_key}{line_friendly}{line_comment}'
        if line_data:
            return f'{line_items}\n{line_data}'
        return line_items
    @override
    def set_from_ini(self, ini: str | list[str]) -> Self:
        # parse incoming list as normal list
        _ = super().set_from_ini(ini)
        return self

    def set_data_from_ini(self, ini_line: str) -> Self:
        ini_line = ini_line.strip()
        # the first portion of data line is a friendly key value and second, if present, is a comment string
        # ensure that last item is either comment or empty:
        # split(';', 1) max 2 parts, add empty string to the end, take first 2 elements back
        friendly_key, comment = (ini_line.split(';', 1) + [''])[:2]
        friendly_key = friendly_key.strip()
        comment = comment.strip()
        # set comment
        self.comment = comment if comment else ''

        # parse ID:
        ms_id: int = -1
        match = re.search(r'multilist([0-9]+)', self.key)
        if match:
            ms_id = int(match.group(1))
        # set friendly_key
        if friendly_key:
            self.friendly_key = friendly_key
            multilist_key_map.set_id(ms_id, self.friendly_key)
        else:
            self.friendly_key = ''
        return self

    @override
    def jsonable(self) -> dict[str, JsonValue]:
        items: list[JsonValue] = []
        for item in self.items:
            if item.is_empty():
                continue
            jsonable: JsonValue = item.jsonable()
            items.append(jsonable)
        result: dict[str, JsonValue] = {
            'friendly_key': self.friendly_key,
            'comment': self.comment,
            'items': items
        }
        return result
    @override
    def set_from_json(self, json_data: JsonValue) -> Self:
        # can only handle dicts
        if not isinstance(json_data, dict):
            return self
        if len(json_data) != 1:
            raise ValueError('Cannot create MultilistValue from JSON data containing more items than one.')
        # key must begin with "multilist"
        key: str = next(iter(json_data.keys()), '')
        if not key.startswith('multilist'):
            raise ValueError(f"Cannot create MultilistValue from JSON data that are not marked with multilist key, expected: 'multilistID', received: {key}")

        # parse ID:
        ms_id: int = -1
        match = re.search(r'multilist([0-9]+)', key)
        if match:
            ms_id = int(match.group(1))

        if isinstance(json_data[key], dict):
            self.key: str = key
            data = json_data[key]
            if isinstance(data, dict):
                # data must have 'items' present
                if not 'items' in data.keys():
                    return self
                if not isinstance(data['items'], list):
                    return self

                # all is ok, use ListValue parser to process items:
                self.value: str = codecs.decode(str(data['items']), 'unicode_escape')
                _ = super().set_from_json(data['items'])
                # set extra data:
                if 'friendly_key' in data.keys():
                    self.friendly_key = str(data['friendly_key'])
                    multilist_key_map.set_id(ms_id, self.friendly_key)
                if 'comment' in data.keys():
                    self.comment = str(data['comment'])
        return self

# =============================================================================
#
# DATA STRUCTURES
#   - IFormattingDict
#   - ITaggedDict (IFormattingDict)
#   - ModManager (ITaggedDict)
#   - ModInfo (ITaggedDict)
#   - Updates (ITaggedDict)
#   - AltDLC (IFormattingDict)
#   - CustomDlc (ITaggedDict)
#
# =============================================================================

@dataclass
class IFormattingDict(IFormatting, ABC):
    ini_separator: str = field(default='\n', repr=False)

    @override
    def is_empty(self) -> bool:
        for attr in self.serializable_fields().values():
            if not attr.is_empty():
                return False
        return True
    @override
    def reset_value(self) -> None:
        for attr in self.serializable_fields().values():
            attr.reset_value()
    @override
    def as_ini(self, formatted: bool = True, pretty: bool = False) -> str:
        if self.is_empty():
            return ""
        result: list[str] = []
        for attr in self.serializable_fields().values():
            if attr.is_empty():
                continue
            result.append(attr.as_ini(formatted))
        return self.ini_separator.join(result)
    @override
    def set_from_ini(self, ini: str | list[str]) -> Self:
        # dict ini values are separated by newlines
        # the resulting RawValue key needs to represent a field on the dict
        # or it will not be accessible and falls thru
        self.reset_value()
        lines: list[str] = []

        if isinstance(ini, str):
            ini = ini.strip()
            lines = ini.split(self.ini_separator)
        else:
            lines = ini

        for line in lines:
            if not is_valid_ini_line(line):
                continue
            temp: RawValue = RawValue().set_from_ini(line)
            # parse successful
            if not temp.is_empty():
                field: IFormatting | None = self.get_field(temp.key)
                if isinstance(field, IFormatting):
                    # dict keys are set during init so it just needs new data
                    _ = field.set_from_ini(line)
        return self

    @override
    def jsonable(self) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {}
        for name, field in self.serializable_fields().items():
            if field.is_empty():
                continue
            result[name] = field.jsonable()
        return result
    @override
    def set_from_json(self, json_data: JsonValue) -> Self:
        if not json_data:
            return self
        if not isinstance(json_data, dict):
            return self

        for key, value in json_data.items():
            attr: IFormatting | None = self.get_field(key)
            if isinstance(attr, IFormatting):
                _ = attr.set_from_json({key: value})
        return self

@dataclass
class ITaggedDict(IFormattingDict, ABC):
    @override
    def as_ini(self, formatted: bool = True, pretty: bool = False) -> str:
        extra_separator: str = self.ini_separator if formatted else ''
        ini: str = super().as_ini(formatted)
        tag: TagValue = TagValue(key=self.key, value=self.key)
        return f'{tag.as_ini(formatted)}{self.ini_separator}{ini}{extra_separator}'

@dataclass
class ModManager(ITaggedDict):
    key: str = field(default='ModManager')
    cmmver: RawValue = field(default_factory=RawValue)
    minbuild: RawValue = field(default_factory=RawValue)

@dataclass
class ModInfo(ITaggedDict):
    key: str = field(default='ModInfo')
    game: RawValue = field(default_factory=RawValue)
    modname: RawValue = field(default_factory=RawValue)
    moddesc: RawValue = field(default_factory=RawValue)
    modver: RawValue = field(default_factory=RawValue)
    moddev: RawValue = field(default_factory=RawValue)
    modsite: UrlValue = field(default_factory=UrlValue)
    updatecode: RawValue = field(default_factory=RawValue)
    nexuscode: RawValue = field(default_factory=RawValue)
    requireddlc: ListValue[DlcValue] = field(default_factory=lambda: ListValue[DlcValue](DlcValue))
    bannerimagename: RawValue = field(default_factory=RawValue)
    sortalternates: BoolValue = field(default_factory=BoolValue)
    requiresenhancedbink: BoolValue = field(default_factory=BoolValue)
    batchinstallreversesort: BoolValue = field(default_factory=BoolValue)

@dataclass
class Updates(ITaggedDict):
    key: str = field(default='UPDATES')
    serverfolder: RawValue = field(default_factory=RawValue)
    blacklistedfiles: ListValue[RawValue] = field(default_factory=lambda: ListValue[RawValue](RawValue))
    additionaldeploymentfolders: ListValue[RawValue] = field(default_factory=lambda: ListValue[RawValue](RawValue))
    additionaldeploymentfiles: ListValue[RawValue] = field(default_factory=lambda: ListValue[RawValue](RawValue))
    nexusupdatecheck: BoolValue = field(default_factory=BoolValue)

@dataclass
class ECondition(EnumValue):
    items: set[str] = field(default_factory=lambda:
    {
        'COND_MANUAL',
        'COND_DLC_PRESENT',
        'COND_DLC_NOT_PRESENT',
        'COND_ANY_DLC_NOT_PRESENT',
        'COND_ANY_DLC_PRESENT',
        'COND_ALL_DLC_PRESENT',
        'COND_ALL_DLC_NOT_PRESENT',
        'COND_SPECIFIC_SIZED_FILES',
    },repr=False)
@dataclass
class EOperation(EnumValue):
    items: set[str] = field(default_factory=lambda:
    {
        'OP_ADD_CUSTOMDLC',
        'OP_ADD_FOLDERFILES_TO_CUSTOMDLC',
        'OP_ADD_MULTILISTFILES_TO_CUSTOMDLC',
        'OP_ENABLE_TLKMERGE_OPTIONKEY',
        'OP_NOTHING'
    },repr=False)
@dataclass
class EAction(EnumValue):
    items: set[str] = field(default_factory=lambda:
    {
        'ACTION_ALLOW_SELECT',
        'ACTION_ALLOW_SELECT_CHECKED',
        'ACTION_DISALLOW_SELECT',
        'ACTION_DISALLOW_SELECT_CHECKED'
    },repr=False)

@dataclass
class AltDlc(IFormattingDict):
    ini_separator: str = field(default=',', repr=False)

    Condition: ECondition = field(default_factory=ECondition)
    ConditionalDLC: ListValue[DlcValue] = field(default_factory=lambda: ListValue[DlcValue](DlcValue))
    ModOperation: EOperation = field(default_factory=EOperation)
    ModAltDLC: PathValue = field(default_factory=PathValue)
    ModDestDLC: PathValue = field(default_factory=PathValue)
    MultiListId: MultilistIDValue = field(default_factory=MultilistIDValue)
    MultiListRootPath: PathValue = field(default_factory=PathValue)
    FlattenMultiListOutput: BoolValue = field(default_factory=BoolValue)
    RequiredFileRelativePaths: ListValue[RawValue] = field(default_factory=lambda: ListValue[RawValue](RawValue))
    RequiredFileSizes: ListValue[RawValue] = field(default_factory=lambda: ListValue[RawValue](RawValue))
    DLCRequirements: ListValue[DlcValue] = field(default_factory=lambda: ListValue[DlcValue](DlcValue))
    FriendlyName: TextValue = field(default_factory=TextValue)
    Description: TextValue = field(default_factory=TextValue)
    CheckedByDefault: BoolValue = field(default_factory=BoolValue)
    OptionGroup: TextValue = field(default_factory=TextValue)
    ApplicableAutoText: TextValue = field(default_factory=TextValue)
    NotApplicableAutoText: TextValue = field(default_factory=TextValue)
    ImageAssetName: RawValue = field(default_factory=RawValue)
    ImageHeight: IntValue = field(default_factory=IntValue)
    OptionKey: RawValue = field(default_factory=RawValue)
    DependsOnKeys: ListValue[RawValue] = field(default_factory=lambda: ListValue[RawValue](RawValue))
    DependsOnMetAction: EAction = field(default_factory=EAction)
    DependsOnNotMetAction: EAction = field(default_factory=EAction)
    SortIndex: IntValue = field(default_factory=IntValue)
    Hidden: BoolValue = field(default_factory=BoolValue)

    @override
    def as_ini(self, formatted: bool = True, pretty: bool = False) -> str:
        # AltDlcs are flattend to single line
        if not pretty:
            return super().as_ini(False)

        # unless we want it to be pretty for no reason:
        lines: list[str] = []
        for item in self.serializable_fields().values():
            if not item.is_empty():
                lines.append('        ' + item.as_ini(pretty=pretty))
        return f'{self.ini_separator}\n'.join(lines)
    @override
    def set_from_ini(self, ini: str | list[str]) -> Self:
        _ = super().set_from_ini(ini)
        _ = self.set_quote_wraps()
        return self

    @override
    def set_from_json(self, json_data: JsonValue) -> Self:
        _ = super().set_from_json(json_data)
        _ = self.set_quote_wraps()
        return self

    def set_quote_wraps(self) -> Self:
        for item in self.ConditionalDLC.items:
            item.quote_wrap = True
        for item in self.DLCRequirements.items:
            item.quote_wrap = True
        return self

@dataclass
class CustomDlc(ITaggedDict):
    key: str = field(default='CUSTOMDLC')
    sourcedirs: ListValue[PathValue] = field(default_factory=lambda: ListValue[PathValue](PathValue))
    destdirs: ListValue[PathValue] = field(default_factory=lambda: ListValue[PathValue](PathValue))
    friendlynames: DictValue[RawValue] = field(default_factory=lambda: DictValue[RawValue](RawValue))
    multilists: DictValue[MultiListValue] = field(default_factory=lambda: DictValue[MultiListValue](MultiListValue))
    # outdated and icompatible dont need option values so I made them just RawValues
    outdatedcustomdlc: ListValue[RawValue] = field(default_factory=lambda: ListValue[RawValue](RawValue))
    incompatiblecustomdlc: ListValue[RawValue] = field(default_factory=lambda: ListValue[RawValue](RawValue))
    altdlc: ArrayValue[AltDlc] = field(default_factory=lambda: ArrayValue[AltDlc](AltDlc))

    @override
    def set_from_ini(self, ini: str | list[str]) -> Self:
        if not isinstance(ini, str):
            return self
        # set multilists.
        # multilists must be first to correctly map id=>friendly_key values
        _ = self.set_multilists_from_ini(ini)

        # set common values.
        _ = super().set_from_ini(ini)
        # set find friendly names.
        # friendly names depend on common values being parsed because
        # I am using destdirs data to detect key values.
        _ = self.set_friendly_names_from_ini(ini)
        return self

    def set_friendly_names_from_ini(self, ini: str) -> Self:
        refNames: set[str] = set[str]()
        # get keys for friendlynames:
        for item in self.destdirs:
            refNames.add(item.value)

        # find values in the passed ini portion
        lines: list[str] = []
        for line in ini.split(self.ini_separator):
            if not is_valid_ini_line(line):
                continue
            match = re.search(r'(.*?)\s*=\s*(.+)', line)
            if not match:
                continue
            key, value = match.groups()
            if key not in refNames:
                continue
            # append item to the list value
            lines.append(line)

        _ = self.friendlynames.set_from_ini(lines)
        return self

    def set_multilists_from_ini(self, ini: str) -> Self:
        refNames: set[str] = set[str]()
        # get keys for friendlynames:
        for item in self.destdirs:
            refNames.add(item.value)

        # find values in the passed ini portion
        lines: list[str] = []
        data_lines: dict[str, str] = {}
        for line in ini.split(self.ini_separator):
            match = re.search(r'(multilist[0-9]+)\s*=\s*(.+)', line)
            if not match:
                continue
            # append item to the list value
            if is_valid_ini_line(line):
                lines.append(line)
            # this line matched multilistID pattern but is not valid line
            # I am using commented lines to pass extra data between formats.
            elif len(match.groups()) == 2:
                data_lines[match.group(1)] = match.group(2)

        _ = self.multilists.set_from_ini(lines)
        # send extra data over to correct multilist
        for key, data in data_lines.items():
            # find index by key
            ms = self.multilists.get_item_by_key(key)
            if ms != None:
                _ = ms.set_data_from_ini(data)
        return self

    @override
    def jsonable(self) -> dict[str, JsonValue]:
        jsonable: dict[str, JsonValue] = super().jsonable()
        result: dict[str, JsonValue] = {}
        keys: list[str] = ['friendlynames', 'multilists']
        try:
            for k, v in jsonable.items():
                if k in keys and v:
                    if not isinstance(v, dict):
                        continue
                    for key, item in v.items():
                        result[key] = item
                else:
                    result[k] = v
        except ValueError:
            raise ValueError("Could not merge 'friendlynames' with its parent dictionary.")

        return result

    @override
    def set_from_json(self, json_data: JsonValue) -> Self:
        # set multilists.
        # multilists must be first to correctly map id=>friendly_key values
        _ = self.set_multilists_from_json(json_data)

        # set common values.
        _ = super().set_from_json(json_data)
        # set find friendly names.
        # friendly names depend on common values being parsed because
        # I am using destdirs data to detect key values.
        _ = self.set_friendly_names_from_json(json_data)
        return self

    def set_friendly_names_from_json(self, json_data: JsonValue) -> Self:
        # find friendly names, they have ID's of destdirs values:
        if not isinstance(json_data, dict):
            return self

        items: dict[str, JsonValue] = {}
        for item in self.destdirs:
            if json_data[item.value]:
                # add the friendly name to the list
                items[item.value] = json_data[item.value]

        _ = self.friendlynames.set_from_json({'friendlynames': items})
        return self

    def set_multilists_from_json(self, json_data: JsonValue) -> Self:
        # get all entries that have their keys begin with "multilist":
        if not isinstance(json_data, dict):
            return self
        filtered_data: dict[str, JsonValue] = {}
        for key, value in json_data.items():
            key = key.strip()
            if key.startswith('multilist'):
                filtered_data[key] = value
        _ = self.multilists.set_from_json({'multilists': filtered_data})
        return self

# =============================================================================
#
# MAIN PARSER CLASS
#
# =============================================================================

@dataclass
class ModDescParser(IFormattingDict):
    uses_key: bool = False
    mod_manager: ModManager = field(default_factory=ModManager)
    mod_info: ModInfo = field(default_factory=ModInfo)
    updates: Updates = field(default_factory=Updates)
    custom_dlc: CustomDlc = field(default_factory=CustomDlc)

    @override
    def as_ini(self, formatted: bool = True, pretty: bool = False) -> str:
        # just strip already
        return super().as_ini(formatted).strip()
    @override
    def set_from_ini(self, ini: str | list[str]) -> Self:
        if isinstance(ini, list):
            raise TypeError(f'{self.__class__.__name__} cannot accept INI input from list variable.')

        # unlike normal dict, these need to find values
        # for each of the sections by key
        self.reset_value()
        ini = ini.strip()
        tag_map = self.get_tag_map()
        ini_lines: list[str] = ini.split(self.ini_separator)
        for i in range(len(ini_lines) - 1, -1, -1):
            line: str = ini_lines[i]
            # try parse tag value
            tag = TagValue().set_from_ini(line)
            if not tag.is_empty() and tag.value in tag_map:
                # doing [i+1:] to get all lines from end of the list minus the tag that was just found
                _ = tag_map[tag.value].set_from_ini(self.ini_separator.join(ini_lines[i+1:]))
        return self

    @override
    def jsonable(self) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {}
        for field in self.serializable_fields().values():
            if field.is_empty():
                continue
            if isinstance(field, ITaggedDict):
                result[field.key] = field.jsonable()
        return result
    @override
    def set_from_json(self, json_data: JsonValue) -> Self:
        json_obj = None
        if isinstance(json_data, str):
            json_obj = cast(JsonValue, json.loads(json_data))
        if not isinstance(json_obj, dict):
            raise TypeError(f'{self.__class__.__name__} could not parse json_data into object.')

        # unlike normal dict, these need to find values
        # for each of the sections by key
        self.reset_value()
        tag_map = self.get_tag_map()
        for json_key, json_value in json_obj.items():
            if json_key in tag_map:
                _ = tag_map[json_key].set_from_json(json_value)
        return self

    def get_tag_map(self) -> dict[str, IFormattingDict]:
        """Get map of .key values from attributes and the attribute itself.
           This is used to target ITaggedDict values."""
        tag_map: dict[str, IFormattingDict] = {}
        for attr in self.serializable_fields().values():
            if isinstance(attr, IFormattingDict):
                tag_map[attr.key] = attr
        return tag_map

# =============================================================================
#
# HELPERS
#
# =============================================================================
def is_valid_ini_line(line: str) -> bool:
    line = line.strip()
    # empty lines
    if not line:
        return False
    # skip commented lines or tags
    if line.startswith(('#',';','[',']')):
        return False
    return True

# =============================================================================
#
# CLI GROUP
#
# =============================================================================
@click.group()
def cli():
    """Python M3Tweaks ModDesc conversion CLI tool."""
    pass

def echo_info(text: str) -> None:
    click.echo(text)
def echo_success(text: str) -> None:
    click.echo(click.style(text, fg='green'))
def echo_fail(text: str) -> None:
    click.echo(click.style(text, fg='red'))

def get_path(filepath: str | None = None) -> Path:
    """
    Resolve filepath:
    - Relative path "moddesc.ini"
    - Absolute path "C:/folder/moddesc.ini"
    """
    if not filepath:
        try:
            filepath = cast(str, click.prompt(text="Enter filepath", default='', show_default=False, type=str))
        except click.Abort as e:
            echo_fail("Aborted by user.")
            filepath = None
    if not filepath:
        echo_fail('Invalid filepath provided.')
        sys.exit(1)

    path = Path(filepath)

    if path.is_absolute():
        return path.resolve()

    elif "/" not in filepath and "\\" not in filepath:
        script_dir = Path(__file__).parent.resolve()
        return (script_dir / path).resolve()
    else:
        return path.resolve()

# =============================================================================
#
# TEST COMMAND
#
# =============================================================================
@cli.command(help="""
Run a test against a file of supported format.

The process parses provided file convers the data back and forth
and then compares the output of same format against the input file.
The test will fail if output differs from input in any way.

The purpose of this function is to provide a somewhat reliable way
to confirm that the program does process data correctly.
""")
@click.argument("filepath", required=False)
def test(filepath: str | None):
    path: Path = get_path(filepath)

    if path.exists():
        if path.suffix == '.ini' or path.suffix == '.json':
            with open(path, "r", encoding="utf-8") as f:
                test = f.read()

                # convert source data into another format
                # and then convert that back to the format of source data
                # the parser should produce the parsed data in the source format
                result: str = ''
                if path.suffix == '.ini':
                    json = ModDescParser().set_from_ini(test).as_json()
                    result = ModDescParser().set_from_json(json).as_ini()
                elif path.suffix == '.json':
                    ini = ModDescParser().set_from_json(test).as_ini()
                    result = ModDescParser().set_from_ini(ini).as_json()

                test = test.strip()
                result = result.strip()

                if test == result:
                    echo_success(f"File '{path.name}' has been tested successfully!")
                else:
                    echo_fail("Test has failed!")

                    console = Console(width=None)
                    diff_lines = list(unified_diff(
                        test.splitlines(keepends=True),
                        result.splitlines(keepends=True),
                        fromfile=path.name,
                        tofile=f'In memory re-converted to {path.suffix} format'
                    ))

                    # Print with syntax highlighting
                    diff_text = ''.join(diff_lines)
                    console.print(Syntax(diff_text, "diff", word_wrap = True))
        else:
            echo_fail(f"Unsupported file format, can only process .ini or .json files, received: {path.suffix}")
    else:
        echo_fail("File not found")
        sys.exit(1)

# =============================================================================
#
# DEBUG COMMAND
#
# =============================================================================
@cli.command(help="""
Prints repr() of the parsed values from the selected file.
""")
@click.argument("filepath", required=False)
def debug(filepath: str | None):
    path: Path = get_path(filepath)

    if path.exists():
        if path.suffix == '.ini' or path.suffix == '.json':
            with open(path, "r", encoding="utf-8") as f:
                data = f.read()

                # convert source data into another format
                obj: ModDescParser | None = None
                if path.suffix == '.ini':
                    obj = ModDescParser().set_from_ini(data)
                elif path.suffix == '.json':
                    obj = ModDescParser().set_from_json(data)

                console = Console(width=None)
                console.print(Pretty(multilist_key_map, indent_size=2))
                console.print(Pretty(obj, indent_size=2))
        else:
            echo_fail(f"Unsupported file format, can only process .ini or .json files, received: {path.suffix}")
    else:
        echo_fail("File not found")
        sys.exit(1)

# =============================================================================
#
# ECHO COMMAND
#
# =============================================================================
@cli.command(help="""
Prints conversion output into the console window.

This will parse the file and outputs it in the same format.
Prettyfied output will be applied for readability.
The purpose of this command is to just read the data in more accessible way.
""")
@click.argument("filepath", required=False)
@click.option('--convert/--no-convert', default=False, help='Instead of outputing the same file format, convert to another and output that.')
def echo(filepath: str | None, convert: bool = False):
    path: Path = get_path(filepath)

    if path.exists():
        if path.suffix == '.ini' or path.suffix == '.json':
            with open(path, "r", encoding="utf-8") as f:
                data = f.read()

                # convert source data into another format
                result: str = ''
                result_format: str = 'json' if path.suffix == '.json' else 'ini'
                lexer: str = 'None'

                # INI
                if path.suffix == '.ini':
                    parser = ModDescParser().set_from_ini(data)
                    result = parser.as_json() if convert else parser.as_ini()
                    result_format = 'json' if convert else 'ini'
                    lexer = 'json' if convert else 'peg'
                # JSON
                elif path.suffix == '.json':
                    parser = ModDescParser().set_from_json(data)
                    result = parser.as_ini() if convert else parser.as_json()
                    result_format = 'ini' if convert else 'json'
                    lexer = 'peg' if convert else 'json'

                if result_format == 'ini':
                    # make it a bit prettier:
                    lines = result.splitlines()
                    for i, line in enumerate(lines):
                        lines[i] = line.strip()
                        if line.startswith('multilist'):
                            multilist: ListValue[PathValue] = ListValue[PathValue](PathValue).set_from_ini(line)
                            lines[i] = multilist.as_ini(pretty=True)
                            continue
                        if line.startswith('altdlc'):
                            altdlc: ArrayValue[AltDlc] = ArrayValue[AltDlc](AltDlc).set_from_ini(line)
                            lines[i] = altdlc.as_ini(pretty=True)
                            continue

                    result = '\n'.join(lines)

                console = Console(width=None)
                console.print(Syntax(result, lexer, word_wrap = True))
        else:
            echo_fail(f"Unsupported file format, can only process .ini or .json files, received: {path.suffix}")
    else:
        echo_fail("File not found")
        sys.exit(1)

# =============================================================================
#
# CONVERT COMMAND
#
# =============================================================================
@cli.command(help="""
Convert data from a file to another format.

Behaviour changes depending on selected file.
- .ini file will get converted to JSON format.
- .json file will get converted to INI format.
""")
@click.argument("filepath", required=False)
def convert(filepath: str | None):
    path: Path = get_path(filepath)

    if path.exists():
        if path.suffix == '.ini' or path.suffix == '.json':
            with open(path, "r", encoding="utf-8") as f:
                data = f.read()

                # convert source data into another format
                result: str = ''
                new_path: Path | None = None

                # INI
                if path.suffix == '.ini':
                    new_path = path.with_suffix('.json')
                    result = ModDescParser().set_from_ini(data).as_json()
                # JSON
                elif path.suffix == '.json':
                    new_path = path.with_suffix('.ini')
                    result = ModDescParser().set_from_json(data).as_ini()
                # append newline at the end
                result = result + '\n'

                if new_path is None:
                    echo_fail("Could not save converted file, path parse failed:")
                    sys.exit(1)

                try:
                    with open(new_path, "w") as nf:
                        _ = nf.write(result)
                    echo_success(f"File '{new_path}' saved successfully!")
                except IOError as e:
                    echo_fail(f"Error saving file: {e}")
        else:
            echo_fail(f"Unsupported file format, can only process .ini or .json files, received: {path.suffix}")
    else:
        echo_fail("File not found")
        sys.exit(1)

# =============================================================================
#
# ENTRY POINT
#
# =============================================================================
if __name__ == "__main__":
    cli()
