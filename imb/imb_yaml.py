import yaml

# Disable aliases during dump (eg. &id001 )
yaml.Dumper.ignore_aliases = lambda *args : True

# Allow yaml sub-document to be embedded as multi-line string when needed
class multiline_str(str): pass
def multiline_str_representer(dumper, data):
    return dumper.represent_scalar(u'tag:yaml.org,2002:str', data, style='|')
yaml.add_representer(multiline_str, multiline_str_representer)

# Set frequently used default params on dump methods
def dump(data, stream=None, default_flow_style=False, sort_keys=False, width=1000):
    return yaml.dump(data, stream, default_flow_style=default_flow_style, sort_keys=sort_keys, width=width)

def dump_all(documents, stream=None, default_flow_style=False, sort_keys=False, width=1000):
    return yaml.dump_all(documents, stream, default_flow_style=default_flow_style, sort_keys=sort_keys, width=width)

# This is just a pass-through since all yaml invokations are now expected to go through this module
def safe_load(*args, **kwargs):
    return yaml.safe_load(*args, **kwargs)