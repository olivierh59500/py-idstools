# Copyright (c) 2013 Jason Ish
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED
# WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF
# MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY DIRECT,
# INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
# HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT,
# STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING
# IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

""" Provides the core of unified2 reading functionality.

::

    usage: from idstools import unified2

"""

from __future__ import print_function

import sys
import os
import os.path
import struct
import collections
import logging
import fnmatch
import time

LOG = logging.getLogger(__name__)

# Record header length.
HDR_LEN = 8

# Record types.
PACKET       = 2
EVENT        = 7
EVENT_IP6    = 72
EVENT_V2     = 104
EVENT_IP6_V2 = 105
EXTRA_DATA   = 110

class Field(object):
    """ A class to represent a field in a unified2 record. Used for
    building the decoders. """

    def __init__(self, name, length, fmt=None):
        self.name = name
        self.length = length
        self._fmt = fmt

    @property
    def fmt(self):
        """Builds a format string for struct.unpack."""
        if self._fmt:
            return self._fmt
        elif self.length == 4:
            return "L"
        elif self.length == 2:
            return "H"
        elif self.length == 1:
            return "B"
        else:
            return None

# Fields in a PACKET record.
PACKET_FIELDS = (
    Field("sensor-id", 4),
    Field("event-id", 4),
    Field("event-second", 4),
    Field("packet-second", 4),
    Field("packet-microsecond", 4),
    Field("linktype", 4),
    Field("length", 4),
    Field("data", None),
)

# Fields in a EVENT record.
EVENT_FIELDS = (
    Field("sensor-id", 4),
    Field("event-id", 4),
    Field("event-second", 4),
    Field("event-microsecond", 4),
    Field("signature-id", 4),
    Field("generator-id", 4),
    Field("signature-revision", 4),
    Field("classification-id", 4),
    Field("priority", 4),
    Field("ip-source", 4, "4s"),
    Field("ip-destination", 4, "4s"),
    Field("sport-itype", 2),
    Field("dport-icode", 2),
    Field("protocol", 1),
    Field("impact-flag", 1),
    Field("impact", 1),
    Field("blocked", 1),
)

# Fields for an IPv6 event.
EVENT_IP6_FIELDS = (
    Field("sensor-id", 4),
    Field("event-id", 4),
    Field("event-second", 4),
    Field("event-microsecond", 4),
    Field("signature-id", 4),
    Field("generator-id", 4),
    Field("signature-revision", 4),
    Field("classification-id", 4),
    Field("priority", 4),
    Field("ip-source", 4, "16s"),
    Field("ip-destination", 4, "16s"),
    Field("sport-itype", 2),
    Field("dport-icode", 2),
    Field("protocol", 1),
    Field("impact-flag", 1),
    Field("impact", 1),
    Field("blocked", 1),
)

# Fields in a v2 event.
EVENT_V2_FIELDS = EVENT_FIELDS + (
    Field("mpls-label", 4),
    Field("vlan-id", 2),
    Field("_pad2", 2),
)

# Fields for an IPv6 v2 event.
EVENT_IP6_V2_FIELDS = EVENT_IP6_FIELDS + (
    Field("mpls-label", 4),
    Field("vlan-id", 2),
    Field("_pad2", 2),
)

# Fields in a UNIFIED_EXTRA_DATA record.
EXTRA_DATA_FIELDS = (
    Field("event-type", 4),
    Field("event-length", 4),
    Field("sensor-id", 4),
    Field("event-id", 4),
    Field("event-second", 4),
    Field("type", 4),
    Field("data-type", 4),
    Field("data-length", 4),
    Field("data", None),
)

class Event(dict):
    """ Class representing a unified2 event. """

    def __init__(self, fields):

        # Create fields to hold extra data and packets associated with
        # this event.
        self["extra-data"] = []
        self["packets"] = []

        # Only v2 events have MPLS and VLAN ids.
        self["mpls-label"] = None
        self["vlan-id"] = None

        for field, value in fields:
            self[field.name] = value

class Packet(dict):
    """ Class to represent a PACKET with a dict-like interface. """

    def __init__(self, *fields, **kwargs):
        for field, value in zip(PACKET_FIELDS, fields):
            self[field.name] = value
        self.update(kwargs)

class ExtraData(dict):
    """ Class to represent EXTRA_DATA with a dict-like interface. """

    def __init__(self, *fields, **kwargs):
        for field, value in zip(EXTRA_DATA_FIELDS, fields):
            self[field.name] = value
        self.update(kwargs)

class Unknown(object):
    """Class to represent an unknown record type.

    In the unlikely case that a record is of an unknown type, an
    instance of `Unknown` will be used to hold the record type and
    buffer.

    """

    def __init__(self, record_type, buf):
        """
        :param type: The record type.
        :param buf: The record buffer.
        """
        self.record_type = record_type
        self.buf = buf

class AbstractDecoder(object):
    """ Base class for decoders. """

    def __init__(self, fields):
        self.fields = fields

        # Calculate the length of the fixed portion of the record.
        self.fixed_len = sum(
            [field.length for field in self.fields if field.length is not None])

        # Build the format string.
        self.format = ">" + "".join(
            [field.fmt for field in self.fields if field.fmt])

class EventDecoder(AbstractDecoder):
    """ Decoder for event type records. """

    def decode(self, buf):
        """Decodes a buffer into an :class:`.Event` object."""
        parts = struct.unpack(self.format, buf)
        return Event(zip(self.fields, parts))

class PacketDecoder(AbstractDecoder):
    """ Decoder for packet type records. """

    def decode(self, buf):
        """Decodes a buffer into a :class:`.Packet` object."""
        parts = struct.unpack(self.format, buf[0:self.fixed_len])
        return Packet(*parts, data=buf[self.fixed_len:])

class ExtraDataDecoder(AbstractDecoder):
    """ Decoder for extra data type records. """

    def decode(self, buf):
        """Decodes a buffer into an :class:`.ExtraData` object."""
        parts = struct.unpack(self.format, buf[0:self.fixed_len])
        return ExtraData(*parts, data=buf[self.fixed_len:])

# Map of decoders keyed by record type.
DECODERS = {
    EVENT:        EventDecoder(EVENT_FIELDS),
    EVENT_IP6:    EventDecoder(EVENT_IP6_FIELDS),
    EVENT_V2:     EventDecoder(EVENT_V2_FIELDS),
    EVENT_IP6_V2: EventDecoder(EVENT_IP6_V2_FIELDS),
    PACKET:       PacketDecoder(PACKET_FIELDS),
    EXTRA_DATA:   ExtraDataDecoder(EXTRA_DATA_FIELDS),
}

class Aggregator(object):
    """A class implementing something like the aggregator pattern to
    aggregate records until an event can be built.

    """

    def __init__(self):
        self.queue = collections.deque()

    def add(self, record):
        """ Add a new record to aggregator.

        :param record: The decoded unified2 record to add.

        :return: If adding a new record allows an event to be
          completed, an :py:class:`.Event` will be returned.
        """

        event = None

        if isinstance(record, Event):
            if self.queue:
                event = self.flush()
            self.queue.append(record)
        elif self.queue:
            self.queue.append(record)
        else:
            LOG.warn("Discarding non-event type while not in event context.")
        return event

    def flush(self):
        """Flush the queue.  This converts the records in the queue
        into an Event.

        If using the Aggregator directly, you'll want to call flush
        after adding all your records to get the final event.

        :returns: An :class:`.Event` or None if there are no records.
        """

        if not self.queue:
            return None

        event = self.queue.popleft()
        assert(isinstance(event, Event))
        while self.queue:
            record = self.queue.popleft()
            assert(not isinstance(record, Event))
            if isinstance(record, Packet):
                event["packets"].append(record)
            elif isinstance(record, ExtraData):
                event["extra-data"].append(record)
        return event

def decode_record(record_type, buf):
    """Decodes a raw record into an object representing the record.

    :param record_type: The type of record.
    :param buf: Buffer containing the raw record.

    :returns: The decoded record as a :class:`.Event`,
      :class:`.Packet`, :class:`.ExtraData` or :class:`.Unknown` if the
      record is of an unknown type.
    """
    if record_type in DECODERS:
        return DECODERS[record_type].decode(buf)
    else:
        return Unknown(record_type, buf)

def read_record(fileobj):
    """Reads a unified2 record from the provided file object.

    :param fileobj: The file like object to read from.  Currently this
      object needs to support read, seek and tell.

    :returns: If a complete record is read a :py:class:`.Record` will
      be returned, otherwise None will be returned.

    If some data is read, but not enough for a whole record, the
    location of the file object will be reset and a
    :exc:`.EOFError` exception will be raised.

    """

    offset = fileobj.tell()
    try:
        buf = fileobj.read(HDR_LEN)
        if not buf:
            # EOF.
            return None
        elif len(buf) < HDR_LEN:
            raise EOFError()
        rtype, rlen = struct.unpack(">LL", buf)
        buf = fileobj.read(rlen)
        if len(buf) < rlen:
            raise EOFError()
        return decode_record(rtype, buf)
    except EOFError as err:
        fileobj.seek(offset)
        raise err

class RecordReader(object):
    """RecordReader reads and decodes unified2 records from a
    file-like object.

    :param fileobj: The file-like object to read from.

    """

    def __init__(self, fileobj):
        self.fileobj = fileobj

        if sys.platform == "darwin" and sys.version_info[0] < 3:
            self.next = self._darwin_next
        else:
            self.next = self._default_next

    def next(self):
        """Return the next record or None if EOF."""
        return self.default_next()

    def _default_next(self):
        return read_record(self.fileobj)

    def _darwin_next(self):
        record = self._default_next()
        if record is None:
            self.fileobj.seek(self.fileobj.tell())
        return record

    def __iter__(self):
        return iter(self.next, None)

class FileRecordReader(object):
    """FileRecordReader reads and decoder unified2 records from one
    or files supplied by filename.

    :param files: A variable number of arguments, specifying the
      unified2 files to read.
    """

    def __init__(self, *files):
        self.files = list(files)
        self.fileobj = open(self.files.pop(0), "rb")
        self.reader = RecordReader(self.fileobj)

    def next(self):
        """Return the next record or None if EOF.

        Records returned will be one of the types :class:`.Event`,
        :class:`.Packet`, :class:`.ExtraData` or :class:`.Unknown` if the
        record is of an unknown type.
        """
        while 1:
            record = self.reader.next()
            if record:
                return record
            if not self.files:
                return
            self.fileobj.close()
            self.fileobj = open(self.files.pop(0), "rb")
            self.reader = RecordReader(self.fileobj)

    def __iter__(self):
        return iter(self.next, None)

class FileEventReader(object):
    """FileEventReader reads events (aggregated records) from one or
    more unified2 files.

    :param files: A variable number of arguments, specifying the
      unified2 files to read.
    """

    def __init__(self, *files):
        self.reader = FileRecordReader(*files)
        self.aggregator = Aggregator()

    def next(self):
        """Return the next :class:`.Event` or None if EOF."""
        while 1:
            record = self.reader.next()
            if not record:
                return self.aggregator.flush()
            else:
                event = self.aggregator.add(record)
                if event:
                    return event

    def __iter__(self):
        return iter(self.next, None)

class SpoolRecordReader(object):
    """SpoolRecordReader reads and decodes records from a unified2
    spool directory.

    Required parameters:

    :param directory: Path to unified2 spool directory.
    :param prefix: Filename prefixes for unified2 log files.
    
    Optional parameters:

    :param init_filename: Filename open on initialization.
    :param init_offset: Offset to seek to on initialization.

    :param tail: Set to true if reading should wait for the next
      record to become available.

    :param rollover_hook: Function to call on rollover of log file,
      the first parameter being the filename being closed, the second
      being the filename being opened.

    """

    def __init__(self,
                 directory,
                 prefix,
                 init_filename = None,
                 init_offset = None,
                 tail = False,
                 rollover_hook = None):
        self.directory = directory
        self.prefix = prefix
        self.tail = tail
        self.rollover_hook = rollover_hook
        self.fileobj = None
        self.reader = None
        self.fnfilter = "%s*" % (self.prefix)

        if init_filename:
            if os.path.exists("%s/%s" % (
                    self.directory, os.path.basename(init_filename))):
                self.open_file(init_filename)
                self.fileobj.seek(init_offset)
                self.reader = RecordReader(self.fileobj)

    def get_filenames(self):
        """Return the filenames (sorted) from the spool directory."""
        return sorted(fnmatch.filter(os.listdir(self.directory), self.fnfilter))

    def open_file(self, filename):
        if self.fileobj:
            closed_filename = self.fileobj.name
            self.fileobj.close()
        else:
            closed_filename = None
        self.fileobj = open("%s/%s" % (
            self.directory, os.path.basename(filename)), "rb")
        self.reader = RecordReader(self.fileobj)
        if self.rollover_hook:
            self.rollover_hook(closed_filename, self.fileobj.name)

    def open_next(self):
        """Open the next available file.  If a new file is opened its
        filename will be returned, otherwise None will be returned.
        """
        filenames = self.get_filenames()

        # If there are no files, just return.
        if not filenames:
            return

        # If we do not have a current fileobj, open the first file.
        if not self.fileobj:
            self.open_file(filenames[0])
            return os.path.basename(self.fileobj.name)

        if os.path.basename(self.fileobj.name) not in filenames:
            # The current file doesn't exist anymore, move on.
            self.open_file(filenames[0])
            return os.path.basename(self.fileobj.name)
        else:
            current_idx = filenames.index(os.path.basename(self.fileobj.name))
            if current_idx + 1 < len(filenames):
                self.fileobj.close()
                self.open_file(filenames[current_idx + 1])
                return os.path.basename(self.fileobj.name)

    def tell(self):
        """Return a tuple containing the filename and offset of the
        file currently being processed.
        """
        if self.fileobj:
            return (self.fileobj.name, self.fileobj.tell())
        return (None, None)

    def _next(self):
        """Return the next decoded unified2 record from the spool
        directory.
        """

        # If we don't have a current file, try to open one.  Failing
        # that just return.
        if self.fileobj == None:
            if not self.open_next():
                return

        # Now try to get a record.  If we can't see if there is a new
        # file and try again.
        try:
            record = self.reader.next()
        except EOFError:
            return
        if record:
            return record
        else:
            while True:
                if self.open_next():
                    try:
                        record = self.reader.next()
                    except EOFError:
                        return
                    if record:
                        return record
                else:
                    return None

    def next(self):
        """Return the next decoded unified2 record from the spool
        directory.  If tail is True, this method will block waiting
        for the next record to become available.
        """
        while True:
            record = self._next()
            if record:
                return record
            if not self.tail:
                return
            else:
                # Sleep for a moment and try again.
                time.sleep(0.01)

    def __iter__(self):
        return iter(self.next, None)

class SpoolEventReader(object):
    """SpoolEventReader reads events (aggregated decoded records) from
    a unified2 spool directory.

    See :class:`.SpoolRecordReader` for constructor parameters.
    """

    def __init__(self,
                 directory,
                 prefix,
                 init_filename = None,
                 init_offset = None,
                 tail = False,
                 rollover_hook = None):

        # Create a SpoolRecordReader.  We purposely don't pass the
        # tail parameter through as we want to handle that here so we
        # can flush the aggregator after a timeout.
        self.reader = SpoolRecordReader(
            directory, prefix, 
            init_filename = init_filename, 
            init_offset = init_offset,
            rollover_hook = rollover_hook)
        self.tail = tail

        self.aggregator = Aggregator()
 
        # Make some methods from the SpoolRecordReader available.
        self.tell = self.reader.tell

    def next(self):
        """Return the next decoded unified2 record from the spool
        directory.  If tail is True, this method will block waiting
        for the next record to become available.
        """
        while True:
            record = self.reader.next()
            if record:
                event = self.aggregator.add(record)
                if event:
                    return event
            else:
                event = self.aggregator.flush()
                if event or not self.tail:
                    return event

                # Sleep for a moment and try again.
                time.sleep(0.01)

    def __iter__(self):
        return iter(self.next, None)
