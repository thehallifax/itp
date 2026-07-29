"""Collector class registration and construction."""
import inspect


class CollectorRegistry:
    _collectors = {}
    _builtins_loaded = False

    @classmethod
    def _ensure_builtins(cls):
        if cls._builtins_loaded:
            return
        cls._builtins_loaded = True
        import importlib
        for module in (
                ".snmp", ".mist", ".fortigate", ".paloalto", ".papercut",
                ".aruba"):
            importlib.import_module(module, "collectors")

    @classmethod
    def register(cls, collector_class):
        name = getattr(collector_class, "name", "")
        if not name:
            raise ValueError("collector classes must define a non-empty name")
        cls._collectors[name] = collector_class
        return collector_class

    @classmethod
    def get(cls, name):
        cls._ensure_builtins()
        return cls._collectors[name]

    @classmethod
    def names(cls):
        cls._ensure_builtins()
        return tuple(sorted(cls._collectors))

    @classmethod
    def create(cls, name, *args, **kwargs):
        return cls.get(name)(*args, **kwargs)

    @classmethod
    def create_configured(cls, name, config, inventory_path, generated_dir):
        """Construct a registered collector from the common runtime paths."""
        collector = cls.get(name)
        available = {
            "config": config,
            "inventory_path": inventory_path,
            "generated_dir": generated_dir,
        }
        parameters = inspect.signature(collector).parameters
        arguments = {
            key: available[key] for key in parameters if key in available}
        return collector(**arguments)

    @classmethod
    def metadata(cls, name):
        collector = cls.get(name)
        return {"name": name, "execution": getattr(collector, "execution", "either")}

    @classmethod
    def execution_eligible(cls, name, settings, runtime_mode):
        execution = settings.get(
            "execution", cls.metadata(name)["execution"])
        return execution in ("either", runtime_mode), execution
