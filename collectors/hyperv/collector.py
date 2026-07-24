from .parser import parse


class HyperVCollector:
    provider = "hyperv"
    read_only = True

    @staticmethod
    def parse(value):
        return parse(value)
