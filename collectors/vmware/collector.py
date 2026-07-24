"""VMware provider adapter."""
from .parser import parse


class VMwareCollector:
    provider = "vmware"
    read_only = True

    @staticmethod
    def parse(value):
        return parse(value)
