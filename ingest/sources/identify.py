"""Identification shared by every connector, modelled on ServiceNow's IRE:
first `serial_number`, then `name` (case-insensitive, discovered aliases
included), then `ip_address`. Network evidence on a service port can also
resolve to the application or database listening there (`ip_port`, from the
discovery scanner's listening-port data, as Service Mapping does).

Every match returns (ci_name, rule); unmatched evidence returns (None, None)
and becomes an unregistered-endpoint / shadow-IT candidate.
"""


class Identifier:
    def __init__(self, cis, aliases=(), listening=()):
        """cis: normalised cmdb_ci rows; aliases: (alias, ci); listening: (ip, port, ci)."""
        self.by_serial = {r["serial_number"]: r["name"] for r in cis if r.get("serial_number")}
        self.by_name = {r["name"].lower(): r["name"] for r in cis}
        self.by_sys_id = {r["sys_id"]: r["name"] for r in cis if r.get("sys_id")}
        self.alias = {a.lower(): ci for a, ci in aliases}
        ips = {}
        for r in cis:
            if r.get("ip_address"):
                ips.setdefault(r["ip_address"], []).append(r["name"])
        self.by_ip = {ip: ns[0] for ip, ns in ips.items() if len(ns) == 1}  # ambiguous IPs never match
        self.listening = {(ip, int(port)): ci for ip, port, ci in listening}

    def match(self, serial=None, name=None, ip=None):
        if serial and serial in self.by_serial:
            return self.by_serial[serial], "serial_number"
        if name:
            key = name.lower()
            if key in self.by_name:
                return self.by_name[key], "name"
            if key in self.alias:
                return self.alias[key], "alias"
            if name in self.by_sys_id:
                return self.by_sys_id[name], "sys_id"
        if ip and ip in self.by_ip:
            return self.by_ip[ip], "ip_address"
        return None, None

    def match_service(self, ip, port, name=None):
        """Server side of a connection: the app/DB listening on ip:port, else the host."""
        if (ip, int(port)) in self.listening:
            return self.listening[(ip, int(port))], "ip_port"
        return self.match(name=name, ip=ip)
