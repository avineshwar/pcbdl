import collections
import enum
import inspect

def plugin(plugin_cls):
	"""Installs a plugin into the class, makes original class behave like the
	   new it's the plugin inheriting the class."""
	for original_cls in plugin_cls.__bases__:
		#print("Installing %s into %s" % (plugin_cls, original_cls))

		# install plugin methods in original class
		for method_name, method in plugin_cls.__dict__.items():
			if method_name in ("__module__", "__doc__", "__init__"):
				continue
			setattr(original_cls, method_name, method)

		original_cls._plugins.append(plugin_cls)

class ConnectDirection(enum.Enum):
	UNKNOWN = 0
	IN = 1
	OUT = 2

class PinType(enum.Enum):
	UNKNOWN = 0
	PRIMARY = 1
	SECONDARY = 2
	POWER_INPUT = 3
	POWER_OUTPUT = 4
	GROUND = 5
	INPUT = 6
	OUTPUT = 7

def _maybe_single(o):
	if isinstance(o, collections.abc.Iterable):
		yield from o
	else:
		yield o

class _PinList(collections.OrderedDict):
	def __getitem__(self, pin_name):
		if isinstance(pin_name, int):
			return tuple(self.values())[pin_name]
		pin_name = pin_name.upper()
		try:
			return super().__getitem__(pin_name)
		except KeyError:
			# try looking slowly through the other names
			for pin in self.values():
				if pin_name.upper() in pin.names:
					return pin
			else:
				raise

	def __iter__(self):
		yield from self.values()

	def __repr__(self):
		return repr(tuple(self.values()))

class Net(object):
	_plugins = []

	def __init__(self, name=None):
		if name is not None:
			name = name.upper()
		self.name = name
		self._connections = collections.OrderedDict()

		for plugin in self._plugins:
			plugin.init(self)

	def connect(self, others, direction=ConnectDirection.UNKNOWN, pin_type=PinType.PRIMARY):
		for other in _maybe_single(others):
			pin = None

			if isinstance(other, Part):
				pin = other.get_pin_to_connect(pin_type)

			if isinstance(other, ParticularPin):
				pin = other

			if isinstance(other, Net):
				raise NotImplementedError("Can't connect nets together yet.")

			if pin is None:
				raise TypeError("Don't know how to get %s pin from %r." % (pin_type.name, other))

			self._connections[pin] = direction
			pin.net = self

	def __lshift__(self, others):
		self.connect(others, ConnectDirection.IN, PinType.PRIMARY)
		return self

	def __rshift__(self, others):
		self.connect(others, ConnectDirection.OUT, PinType.PRIMARY)
		return self

	MAX_REPR_CONNECTIONS = 10
	def __repr__(self):
		connected = self.connections
		if len(connected) >= self.MAX_REPR_CONNECTIONS:
			inside_str = "%d connections" % (len(connected))
		elif len(connected) == 0:
			inside_str = "unconnected"
		elif len(connected) == 1:
			inside_str = "connected to " + repr(connected[0])
		else:
			inside_str = "connected to " + repr(connected)[1:-1]
		return "%s(%s)" % (self, inside_str)

	def __str__(self):
		if self.name is None:
			return "AnonymousNet"
		return "%s" % self.name

	@property
	def connections(self):
		return tuple(self._connections.keys())

class Pin(object):
	"""Generic Pin instance of a Part class, but no particular Part instance.
	   Contains general information about the pin (but it could be for any part of that type), nothing specific to a specific part."""

	_plugins = []
	well_name = None

	def __init__(self, names, numbers=None, type=PinType.UNKNOWN, well=None):
		if isinstance(names, str):
			names = (names,)
		self.names = tuple(name.upper() for name in names)
		self.numbers = numbers
		self.type = type
		self.well_name = well

		for plugin in self._plugins:
			plugin.init(self)

	@property
	def name(self):
		return self.names[0]

	@property
	def number(self):
		return self.numbers[0]

	def __str__(self):
		return "Pin %s" % (self.name)
	__repr__ = __str__

class ParticularPin(Pin):
	"""A pin from an actual instance of a Part, might be connected to nets. Each Part instance has different ParticularPin instances."""
	_plugins = []
	_create_anonymous_net = Net
	_net = None

	def __init__(self, part_instance, part_pin_instance, number=None):
		# copy state of the Pin to be inherited, then continue as if the parent class always existed that way
		self.__dict__.update(part_pin_instance.__dict__.copy())
		# no need to call Pin.__init__
		self._part_pin_instance = part_pin_instance

		# save arguments
		self.part = part_instance

		if number is not None:
			self.numbers = (number,)
		assert self.numbers is not None, "this Pin really should have had real pin numbers assigned by now"

		well_name = self._part_pin_instance.well_name
		if well_name is not None:
			try:
				self.well = self.part.pins[well_name]
			except KeyError:
				raise KeyError("Couldn't find voltage well pin %s on part %r" % (well_name, part_instance))
			if self.well.type not in (PinType.POWER_INPUT, PinType.POWER_OUTPUT):
				raise ValueError("The chosen well pin %s is not a power pin (but is %s)" % (self.well, self.well.type))

	@property
	def net(self):
		if self._net is None:
			fresh_net = ParticularPin._create_anonymous_net()
			fresh_net.connect(self, direction=ConnectDirection.UNKNOWN) # This indirectly sets self.net
		return self._net
	@net.setter
	def net(self, new_net):
		if self._net is not None:
			# TODO: Maybe just unify the existing net and the new
			# net and allow this.
			raise ValueError("%s pin is already connected to a net (%s). Can't connect to %s too." %
				(self, self._net, new_net))

		self._net = new_net

	def __lshift__(self, others):
		if self._net is None:
			# don't let the net property create a new one,
			# we want to dictate the direction to that Net
			ParticularPin._create_anonymous_net() >> self
		return self.net << others

	def __rshift__(self, others):
		if self._net is None:
			# don't let the net property create a new one,
			# we want to dictate the direction to that Net
			ParticularPin._create_anonymous_net() << self
		return self.net >> others

	def __str__(self):
		return "%r.%s" % (self.part, self.name)
	__repr__ = __str__

class Part(object):
	_plugins = []
	PINS = []
	REFDES_PREFIX = "UNK"
	value = ""

	def __init__(self, value=None, refdes=None, package=None, populated=True):
		if value is None:
			value = self.value
		self.value = value
		self._refdes = refdes

		if package is not None:
			self.package = package
		self.populated = populated

		self._generate_pin_instances(self.PINS)

		for plugin in self._plugins:
			plugin.init(self)

	def _generate_pin_instances(self, pin_names):
		# syntactic sugar, .PIN list might have only names instead of the long form Pin instances
		for i, maybenames in enumerate(self.PINS):
			if not isinstance(maybenames, Pin):
				self.PINS[i] = Pin(maybenames)

		self.pins = _PinList()
		for i, part_class_pin in enumerate(self.PINS):
			# if we don't have an assigned pin number, generate one
			pin_number = str(i) if part_class_pin.numbers is None else None

			pin = ParticularPin(self, part_class_pin, pin_number)
			self.pins[pin.name] = pin

			# save the pin as an attr for this part too
			for name in pin.names:
				self.__dict__[name] = pin

	@property
	def refdes(self):
		if self._refdes is None:
			return "%s?%05x" % (self.REFDES_PREFIX, id(self) // 32 & 0xfffff)
		return self._refdes
	@refdes.setter
	def refdes(self, new_value):
		self._refdes = new_value.upper()

	def __repr__(self):
		return self.refdes

	def __str__(self):
		return "%s - %s%s" % (self.refdes, self.value, " DNS" if not self.populated else "")

	def get_pin_to_connect(self, pin_type): # pragma: no cover
		assert isinstance(pin_type, PinType)
		raise NotImplementedError("Don't know how to get %s pin from %r" % (pin_type.name, self))
