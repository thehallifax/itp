from .parser import parse


class ProxmoxCollector:
    provider = "proxmox"
    read_only = True

    @staticmethod
    def parse(value):
        return parse(value)
