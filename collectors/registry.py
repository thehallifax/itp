"""Collector class registration and construction."""


class CollectorRegistry:
    _collectors = {}

    @classmethod
    def register(cls, collector_class):
        name = getattr(collector_class, "name", "")
        if not name:
            raise ValueError("collector classes must define a non-empty name")
        cls._collectors[name] = collector_class
        return collector_class

    @classmethod
    def get(cls, name):
        return cls._collectors[name]

    @classmethod
    def names(cls):
        return tuple(sorted(cls._collectors))

    @classmethod
    def create(cls, name, *args, **kwargs):
        return cls.get(name)(*args, **kwargs)

    @classmethod
    def metadata(cls, name):
        collector = cls.get(name)
        return {"name": name, "execution": getattr(collector, "execution", "either")}
