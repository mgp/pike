import collections
import connector
import converters
import os
import tempfile
import threading
import unittest

class TestPrimitivesConverter(unittest.TestCase):
  def _test_conversion(self, values, to_bytes_converter, from_bytes_converter):
    remainder, value = from_bytes_converter('')
    self.assertIsNone(value)

    added_remainder = 'abc'
    for value in values:
      serialized = to_bytes_converter(value)
      remainder, deserialized = from_bytes_converter(serialized)
      self.assertEqual(value, deserialized)
      self.assertEqual(remainder, '')

      serialized += added_remainder
      remainder, deserialized = from_bytes_converter(serialized )
      self.assertEqual(value, deserialized)
      self.assertEqual(remainder, added_remainder)

  def test_booleans(self):
    values = (True, False)
    to_bytes_converter = converters.Primitives.bool_to_bytes
    from_bytes_converter = converters.Primitives.bytes_to_bool
    self._test_conversion(values, to_bytes_converter, from_bytes_converter)

  def test_ints(self):
    values = (-1, 0, 9999)
    to_bytes_converter = converters.Primitives.int_to_bytes
    from_bytes_converter = converters.Primitives.bytes_to_int
    self._test_conversion(values, to_bytes_converter, from_bytes_converter)

  def test_floats(self):
    values = (-1.5, 0.0, 22.0/7)
    to_bytes_converter = converters.Primitives.float_to_bytes
    from_bytes_converter = converters.Primitives.bytes_to_float
    self._test_conversion(values, to_bytes_converter, from_bytes_converter)

  def test_single_string(self):
    for s, expected in (('', '0 '), ('a', '1 a'), ('abc', '3 abc')):
      serialized = converters.Primitives.string_to_bytes(s)
      self.assertEqual(serialized, expected)

      remainder, deserialized = converters.Primitives.bytes_to_string(serialized)
      self.assertEqual(remainder, '')
      self.assertEqual(deserialized, s)

  def test_single_string_by_byte(self):
    s = 'abc'
    serialized = converters.Primitives.string_to_bytes(s)

    # Every character up to the last character should return WAIT_FOR_MORE
    data = ''
    for c in serialized[:-1]:
      data += c
      remainder, deserialized = converters.Primitives.bytes_to_string(data)
      self.assertEqual(remainder, data)
      self.assertIsNone(deserialized)

    # Appending the last character should deserialize the string.
    data += serialized[-1]
    remainder, deserialized = converters.Primitives.bytes_to_string(data)
    self.assertEqual(remainder, '')
    self.assertEqual(deserialized, s)

  def test_multiple_strings(self):
    s = ('', 'a', 'abc')
    serialized = tuple(converters.Primitives.string_to_bytes(e) for e in s)
    remainder = ''.join(serialized)

    # First deserialization should deserialize the first string.
    remainder, deserialized = converters.Primitives.bytes_to_string(remainder)
    self.assertEqual(remainder, serialized[1] + serialized[2])
    self.assertEqual(deserialized, s[0])

    # Deserializing again should deserialize the next string.
    remainder, deserialized = converters.Primitives.bytes_to_string(remainder)
    self.assertEqual(remainder, serialized[2])
    self.assertEqual(deserialized, s[1])

    # Last deserialization should deserialize the last string.
    remainder, deserialized = converters.Primitives.bytes_to_string(remainder)
    self.assertEqual(remainder, '')
    self.assertEqual(deserialized, s[2])

  def test_converters(self):
    values = (True, 5, 22.0/7, 'mgp')
    value_converters = (
        converters.Primitives.BOOL_CONVERTER,
        converters.Primitives.INT_CONVERTER,
        converters.Primitives.FLOAT_CONVERTER,
        converters.Primitives.STRING_CONVERTER)
    for value, converter in zip(values, value_converters):
      serializer = converter.get_serializer(value)
      serialized = serializer.get_bytes()
      self.assertEqual(serializer.get_bytes(), '')

      deserializer = converter.get_deserializer()
      remainder, status, deserialized = deserializer.put_bytes(serialized)
      self.assertEqual(remainder, '')
      self.assertEqual(status, connector.Deserializer.DONE)
      self.assertEqual(deserialized, value)

class TestFileReader(unittest.TestCase):
  def _test_file_reader(self, file_contents, read_size):
    file_name = os.path.join(tempfile.gettempdir(), 'testfile')

    try:
      with open(file_name, 'wb') as f:
        f.write(file_contents)
      reader = converters._FileStreamSerializer.FileReader(file_name, read_size)
      all_read_bytes = collections.deque()
      event = threading.Event()

      while True:
        read_bytes = reader.get_bytes(event.set)
        if read_bytes is None:
          # Wait for the callback and then proceed.
          event.wait()
          event = threading.Event()
        elif len(read_bytes):
          all_read_bytes.append(read_bytes)
        else:
          break

      read_file_contents = ''.join(all_read_bytes)
      self.assertEqual(read_file_contents, file_contents)
    finally:
      os.remove(file_name)

  def test_empty_file(self):
    read_size = 1024
    file_contents = ''
    self._test_file_reader(file_contents, read_size)

  def test_single_read(self):
    read_size = 1024
    file_contents = 'abc'
    self.assertLessEqual(len(file_contents), read_size)
    self._test_file_reader(file_contents, read_size)

  def test_multi_reads(self):
    read_size = 1024
    file_block = ''.join(chr(i) for i in xrange(256))
    file_contents = ''.join(file_block for j in xrange(1000))
    self.assertGreater(len(file_contents), read_size)
    self._test_file_reader(file_contents, read_size)

class TestFileWriter(unittest.TestCase):
  def _test_file_writer(self, file_contents, fill_rate, write_size):
    file_name = os.path.join(tempfile.gettempdir(), 'testfile')

    try:
      writer = converters._FileStreamDeserializer.FileWriter(
          file_name, len(file_contents), write_size)
      bytes_read = 0
      bytes_written = 0
      data = ''
      event = threading.Event()

      while True:
        remainder, status = writer.put_bytes(data, event.set)
        bytes_written += (len(data) - len(remainder))
        data = remainder
        if status == connector.StreamDeserializer.DONE:
          break
        elif status == connector.StreamDeserializer.WAIT_FOR_MORE:
          # Append more data for the next write.
          data += file_contents[bytes_read:(bytes_read + fill_rate)]
          bytes_read += fill_rate
        else:
          # Wait for the callback and then proceed.
          event.wait()
          event = threading.Event()

      self.assertEqual(data, '')
      self.assertEqual(bytes_written, len(file_contents))

      with open(file_name, 'rb') as f:
        written_file_contents = f.read()
      self.assertEqual(written_file_contents, file_contents)
    finally:
      os.remove(file_name)

  def test_empty_file(self):
    file_contents = ''
    fill_rate = 1
    write_size = 1024
    self._test_file_writer(file_contents, fill_rate, write_size)

  def test_single_write(self):
    file_contents = 'abc'
    fill_rate = 1024
    write_size = 1024
    self._test_file_writer(file_contents, fill_rate, write_size)

  def test_multi_writes(self):
    file_block = ''.join(chr(i) for i in xrange(256))
    file_contents = ''.join(file_block for j in xrange(1000))
    write_size = 1024 

    # One write does not fill _data.
    fill_rate = 1000
    self._test_file_writer(file_contents, fill_rate, write_size)

    # One write can fill _data and part of _next_data.
    fill_rate = 1500
    self._test_file_writer(file_contents, fill_rate, write_size)

    # One write can fill both _data_ and _next_data.
    fill_rate = 2500
    self._test_file_writer(file_contents, fill_rate, write_size)

if __name__ == '__main__':
    unittest.main()

