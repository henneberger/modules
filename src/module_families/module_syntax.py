from __future__ import annotations

from copy import deepcopy

from .interfaces import validate_interface_reference


def interface_reference(value):
    if isinstance(value, str):
        name, separator, version = value.rpartition("@")
        if not separator:
            raise ValueError("interface references use name@version, for example kb.search@1")
        value = {"id": name, "version": version}
    return validate_interface_reference(value)


def associated_paths(value, signatures):
    value = deepcopy(value)

    def expand(term):
        if not isinstance(term, str):
            return term
        owner, separator, name = term.partition(".")
        types = signatures.get(owner, {}).get("associated", {}).get("types", {})
        if not separator or name not in types:
            raise ValueError(f"unknown associated type path: {term}")
        return {"from": term, "kind": types[name]}

    types = value.get("types", {})
    if isinstance(types, dict):
        value["types"] = {name: expand(term) for name, term in types.items()}
    sharing = value.get("sharing", [])
    if not isinstance(sharing, list) or any(not isinstance(pair, list) or len(pair) != 2 for pair in sharing):
        raise ValueError("associated sharing requires pairs of type paths or terms")
    if "sharing" in value:
        value["sharing"] = [[expand(left), expand(right)] for left, right in sharing]
    return value


def normalize_authoring(document, *, graph=False):
    if not isinstance(document, dict):
        raise ValueError("module document must be a table")
    header = document.get("module")
    if isinstance(header, dict) and "provides" in header:
        header["provides"] = interface_reference(header["provides"])
    ports = document.get("ports", {})
    if isinstance(ports, dict):
        for name, port in list(ports.items()):
            if isinstance(port, str):
                ports[name] = {"requires": interface_reference(port)}
            elif isinstance(port, dict) and "requires" in port:
                port["requires"] = interface_reference(port["requires"])
    if not graph:
        return document
    nodes = document.get("nodes", {})
    links = document.setdefault("links", {})
    views = document.setdefault("views", {})
    if not all(isinstance(value, dict) for value in (nodes, links, views)):
        raise ValueError("nodes, links, and views must be tables")
    for name, node in nodes.items():
        if not isinstance(node, dict):
            raise ValueError(f"node {name} must be a table")
        if "use" in node:
            if "select" in node:
                raise ValueError(f"node {name} declares both use and select")
            use = node.pop("use")
            if not isinstance(use, str) or use.count("/") != 1:
                raise ValueError(f"node {name}: use must be family/member")
            family, member = use.split("/")
            node["select"] = {"family": family, "member": member}
        selector = node.get("select")
        if isinstance(selector, dict) and "requires" in selector:
            selector["requires"] = interface_reference(selector["requires"])
        arguments = node.pop("with", {})
        if not isinstance(arguments, dict):
            raise ValueError(f"node {name}: with must map dependency slots to modules")
        for slot, binding in arguments.items():
            edge = name + "." + slot
            if edge in links or edge in views:
                raise ValueError(f"dependency {edge} is declared more than once")
            if isinstance(binding, str):
                links[edge] = binding
            elif isinstance(binding, dict) and "module" in binding and not set(binding) - {"module", "exports", "associated", "instances", "where"}:
                links[edge] = binding["module"]
                views[edge] = {key: value for key, value in binding.items() if key != "module"}
            else:
                raise ValueError(f"dependency {edge}: expected a module name or a signature view with module = name")
    return document
