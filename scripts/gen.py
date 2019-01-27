import argparse
import json
import yaml

import sys


# Python code generator to create new troposphere classes from the
# AWS resource specification.
#
# This gnerator works by reading in an AWS resource specification json file.
# The resources and properties are split apart to align with a given output
# file. In other words, a type such as AWS::Batch::JobDefinition will be
# put into the batch.py file.
#
# Since there are usually discrepencies in the docs or spec files plus the
# need for validation routines to be included, there is now a YAML file to
# provide these overrides. The validators can override both at a class level
# to validate multiple properties or object consistency using a class Mixin,
# and a property level to validate the contents of that property using a
# simple function. The property required field can also be overriden.
#
# The validators for a given file are now put into a separate file,
# ending in _validators.py (such as batch_validators.py).
#
# Care is given to the output file to ensure pycodestyle and pyflakes tests
# will still pass. This incudes import declarations, class output ordering,
# and spacing considerations.
#
# Todo:
# - Currently only handles the single files (not the all-in-one)
#   (Note: but will deal with things like spec/GuardDuty*)
# - Handle adding in validators
# - Verify propery dependency/ordering in the file
# - Needs better error checking
# - Need to figure out the correct Timestamp type

copyright_header = """\
# Copyright (c) 2012-2019, Mark Peek <mark@peek.org>
# All rights reserved.
#
# See LICENSE file for full license.
#
# *** Do not modify - this file is autogenerated ***
# Resource specification version: %s

"""
spec_version = ""

class Override(object):
    """Handle overrides to the base resource specification.

    While the resource specification is the main source of truth for
    CloudFormation resources and properties, there are sometimes bugs
    or issues which require manual overrides. In addition, this handles
    specifying more specific property and object validation functions.
    """

    def __init__(self, filename):
        self.base = 'troposphere/'
        self.filename = filename
        try:
            self.override = yaml.load(open(self.base + filename + ".yaml"))
        except (OSError, IOError):
            self.override = {}

    def get_header(self):
            return self.override.get('header', "")

    def get_required(self, class_name, prop):
        if self.override:
            try:
                v = self.override['classes'][class_name][prop]['required']
                return v
            except KeyError:
                return None

    def get_validator(self, class_name, prop):
        if self.override:
            try:
                v = self.override['classes'][class_name][prop]['validator']
                return v.lstrip('common/')
            except KeyError:
                return None

    def get_class_validator(self, class_name):
        if self.override:
            try:
                v = self.override['classes'][class_name]['validator']
                return v.lstrip('common/')
            except KeyError:
                return None

    def get_validator_list(self):
        """Return a list of validators specified in the override file"""
        ignore = [
            'dict',
        ]
        vlist = []
        if not self.override:
            return vlist

        for k, v in self.override['classes'].items():
            if 'validator' in v:
                validator = v['validator']
                if validator not in ignore and validator not in vlist:
                    vlist.append(validator)

        for k, v in self.override['classes'].items():
            for kp, vp in v.items():
                if 'validator' in vp:
                    validator = vp['validator']
                    if validator not in ignore and validator not in vlist:
                        vlist.append(validator)
        return sorted(vlist)


class Node(object):
    """Node object for building a per-file/service dependecy tree.

    Simple node object for creating and traversing the resource and
    property dependencies to emit code resources in a well-defined order.
    """

    def __init__(self, name, props, resource_name):
        self.name = name
        self.props = props
        self.resource_name = resource_name
        self.children = []

    def add_child(self, node):
        self.children.append(node)


class File(object):
    """Decribes a file object which contains resources for a given AWS service.

    The main output of this generator is a file containing all the property
    and resource classes for a given AWS service. This handles various needs
    such as imported objects, predictive ordering objects, and handling the
    type and validation overrides. The objects are mapped into the file
    based on the resource type.
    """

    def __init__(self, filename):
        self.filename = filename
        self.imports = {}
        self.properties = {}
        self.resources = {}
        self.resource_names = {}
        self.override = Override(filename)

    def add_property(self, class_name, property_spec):
        self.properties[class_name] = property_spec

    def add_resource(self, class_name, resource_spec, resource_name):
        self.resources[class_name] = resource_spec
        self.resource_names[class_name] = resource_name

    def _output_tags(self):
        """Look for a Tags object to output a Tags import"""
        for class_name, properties in sorted(self.resources.items()):
            for key, value in sorted(properties.iteritems()):
                validator = self.override.get_validator(class_name, key)
                if key == 'Tags' and validator is None:
                    print "from troposphere import Tags"
                    return
        for class_name, properties in sorted(self.properties.items()):
            for key, value in sorted(properties.iteritems()):
                validator = self.override.get_validator(class_name, key)
                if key == 'Tags' and validator is None:
                    print "from troposphere import Tags"
                    return

    def _check_type(self, check_type, properties):
        """Decode a properties type looking for a specific type."""
        if 'PrimitiveType' in properties:
            return properties['PrimitiveType'] == check_type
        if properties['Type'] == 'List':
            if 'ItemType' in properties:
                return properties['ItemType'] == check_type
            else:
                return properties['PrimitiveItemType'] == check_type
        return False

    def _walk_for_type(self, check_type):
        """Walk the resources/properties looking for a specific type."""
        for class_name, properties in sorted(self.resources.items()):
            for key, value in sorted(properties.iteritems()):
                if self._check_type(check_type, value):
                    return True
        for class_name, properties in sorted(self.properties.items()):
            for key, value in sorted(properties.iteritems()):
                if self._check_type(check_type, value):
                    return True

        return False

    def _get_property_type(self, value):
        """Decode the values type and return a non-primitive property type."""
        if 'PrimitiveType' in value:
            return None
        if value['Type'] == 'List':
            if 'ItemType' in value:
                return value['ItemType']
            else:
                return None
        elif value['Type'] == 'Map':
            return None
        else:
            # Non-primitive (Property) name
            return value['Type']

    def _get_type_list(self, props):
        """Return a list of non-primitive types used by this object."""
        type_list = []
        for k, v in props.items():
            t = self._get_property_type(v)
            if t is not None:
                type_list.append(t)
        return sorted(type_list)

    def _output_validators(self):
        """Output common validator types based on usage."""
        if self._walk_for_type('Boolean'):
            print "from .validators import boolean"
        if self._walk_for_type('Integer'):
            print "from .validators import integer"
        vlist = self.override.get_validator_list()
        for override in vlist:
            if override.startswith('common/'):
                override = override.lstrip('common/')
                filename = "validators"
            else:
                filename = "%s_validators" % self.filename
            print "from .%s import %s" % (filename, override)

    def _output_imports(self):
        """Output imports for base troposphere class types."""
        if self.resources:
            print "from . import AWSObject"
        if self.properties:
            print "from . import AWSProperty"

    def build_tree(self, name, props, resource_name=None):
        """Build a tree of non-primitive typed dependency order."""
        n = Node(name, props, resource_name)
        prop_type_list = self._get_type_list(props)
        if not prop_type_list:
            return n
        prop_type_list = sorted(prop_type_list)
        for prop_name in prop_type_list:
            if prop_name == 'Tag':
                continue
            child = self.build_tree(prop_name, self.properties[prop_name])
            if child is not None:
                n.add_child(child)
        return n

    def output_tree(self, t, seen):
        """Given a dependency tree of objects, output it in DFS order."""
        if not t:
            return
        for c in t.children:
            self.output_tree(c, seen)
        if t.name in seen:
            return
        seen[t.name] = True
        if t.resource_name:
            output_class(t.name, t.props, self.override, t.resource_name)
        else:
            output_class(t.name, t.props, self.override)

    def output(self):
        """Output the generated source file."""
        print copyright_header % spec_version,
        self._output_imports()
        self._output_tags()
        self._output_validators()
        header = self.override.get_header()
        if header:
            print
            print
            print header.rstrip()

        seen = {}
        for class_name, properties in sorted(self.resources.items()):
            resource_name = self.resource_names[class_name]
            t = self.build_tree(class_name, properties, resource_name)
            self.output_tree(t, seen)


class Resources(object):
    def __init__(self):
        self.files = {}

    def _filename_map(self, name):
        return name.split(":")[2].lower()

    def get_file(self, aws_name):
        filename = self._filename_map(aws_name)
        if filename not in self.files:
            self.files[filename] = File(filename)
        return self.files[filename]

    def output_file(self, name):
        self.files[name].output()

    def output_files(self):
        for name, file in sorted(self.files.items()):
            file.output()


def get_required(value):
    return value['Required']


map_type = {
    'Boolean': 'boolean',
    'Double': 'float',
    'Integer': 'integer',
    'Json': 'dict',
    'Long': 'integer',
    'String': 'basestring',
    'Timestamp': 'basestring',
}


map_type3 = {
    'Boolean': 'bool',
    'Double': 'float',
    'Integer': 'int',
    'Json': 'dict',
    'Long': 'int',
    'String': 'str',
    'Timestamp': 'str',
}


def get_type(value):
    if 'PrimitiveType' in value:
        return map_type.get(value['PrimitiveType'], value['PrimitiveType'])
    if value['Type'] == 'List':
        if 'ItemType' in value:
            return "[%s]" % value['ItemType']
        else:
            return "[%s]" % map_type.get(value['PrimitiveItemType'])
    elif value['Type'] == 'Map':
        return 'dict'
    else:
        # Non-primitive (Property) name
        return value['Type']

    import pprint
    pprint.pprint(value)
    raise ValueError("get_type")


def get_type3(value):
    if 'PrimitiveType' in value:
        return map_type3.get(value['PrimitiveType'], value['PrimitiveType'])
    if value['Type'] == 'List':
        if 'ItemType' in value:
            return "[%s]" % value['ItemType']
        else:
            return "[%s]" % map_type3.get(value['PrimitiveItemType'])
    elif value['Type'] == 'Map':
        return 'dict'
    else:
        # Non-primitive (Property) name
        return value['Type']

    import pprint
    pprint.pprint(value)
    raise ValueError("get_type")


def output_class(class_name, properties, override, resource_name=None):
    print
    print
    class_validator = override.get_class_validator(class_name)
    mixin = ""
    if class_validator:
        mixin = "%s, " % class_validator
    linebreak = ""
    if len(mixin) > 28:
        linebreak = "\n%s" % (' '*8)
    if resource_name:
        print 'class %s(%s%sAWSObject):' % (class_name, linebreak, mixin)
        print '    resource_type = "%s"' % resource_name
        print
    else:
        print 'class %s(%s%sAWSProperty):' % (class_name, linebreak, mixin)

    # Output the props dict
    print '    props = {'
    for key, value in sorted(properties.iteritems()):
        if key == 'Tags':
            value_type = "Tags"
        else:
            value_type = get_type(value)

        custom_validator = override.get_validator(class_name, key)
        if custom_validator is not None:
            value_type = custom_validator

        required = override.get_required(class_name, key)
        if required is None:
            required = get_required(value)

        # Wrap long names for pycodestyle
        if len(key) + len(value_type) < 55:
            print "        '%s': (%s, %s)," % (
                key, value_type, required)
        else:
            print "        '%s':\n            (%s, %s)," % (
                key, value_type, required)
    print '    }'


def output_class_stub(class_name, properties, resource_name=None):
    print
    print
    if resource_name:
        print 'class %s(AWSObject):' % class_name
        print '    resource_type: str'
        print
        sys.stdout.write('    def __init__(self, title')
    else:
        print 'class %s(AWSProperty):' % class_name
        print
        sys.stdout.write('    def __init__(self')

    for key, value in sorted(properties.iteritems()):
        if key == 'Tags':
            value_type = "Tags"
        else:
            value_type = get_type3(value)

        if value_type.startswith("["):  # Means that args are a list
            sys.stdout.write(', %s:List%s=...' % (key, value_type))
        else:
            sys.stdout.write(', %s:%s=...' % (key, value_type))

    print ') -> None: ...'
    print

    for key, value in sorted(properties.iteritems()):
        if key == 'Tags':
            value_type = "Tags"
        else:
            value_type = get_type3(value)

        if value_type.startswith("["):  # Means that args are a list
            print '    %s: List%s' % (key, value_type)
        else:
            print '    %s: %s' % (key, value_type)


def process_file(filename, stub=False):
    f = open(filename)
    j = json.load(f)

    if 'PropertyTypes' in j:
        for property_name, property_dict in j['PropertyTypes'].items():
            if property_name == "Tag":
                print "from troposphere import Tags"
                print
                continue
            class_name = property_name.split('.')[1]
            properties = property_dict['Properties']
            if stub:
                output_class_stub(class_name, properties)
            else:
                output_class(class_name, properties)

    for resource_name, resource_dict in j['ResourceType'].items():
        class_name = resource_name.split(':')[4]
        properties = resource_dict['Properties']
        if stub:
            output_class_stub(class_name, properties, resource_name)
        else:
            output_class(class_name, properties, resource_name)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--stub', action='store_true', default=False)
    parser.add_argument('--name', action="store")
    parser.add_argument('filename', nargs='+')
    args = parser.parse_args()

    f = open(args.filename[0])
    j = json.load(f)

    spec_version = j['ResourceSpecificationVersion']

    r = Resources()

    for resource_name, resource_dict in sorted(j['ResourceTypes'].items()):
        f = r.get_file(resource_name)
        class_name = resource_name.split(':')[4]
        properties = resource_dict['Properties']
        f.add_resource(class_name, properties, resource_name)

    for property_name, property_dict in sorted(j['PropertyTypes'].items()):
        if property_name == "Tag":
            continue
        f = r.get_file(property_name)
        class_name = property_name.split('.')[1]
        properties = property_dict['Properties']
        f.add_property(class_name, properties)

    if args.name:
        r.output_file(args.name)
    else:
        r.output_files()
