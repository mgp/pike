import collections
import connector
import os
import threading

class Primitives:
  @staticmethod
  def bool_to_bytes(data):
    """Converts the given boolean as a string of bytes.
    """
    return str(bool(data))

  @staticmethod
  def bytes_to_bool(data):
    """
    """
    if data.startswith('True'):
      return data[4:], True, 
    elif data.startswith('False'):
      return data[5:], False
    return data, None

  @staticmethod
  def _space_terminated_value(data):
    sep_index = data.find(' ')
    if sep_index < 0:
      return data, None
    else:
      remainder = data[(sep_index + 1):]
      value = data[:sep_index]
      return remainder, value

  @staticmethod
  def int_to_bytes(data):
    """Returns a string representation of the given integer.
    """
    return '%d ' % data

  @staticmethod
  def bytes_to_int(data):
    remainder, value = Primitives._space_terminated_value(data)
    if value is not None:
      value = int(value)
    return remainder, value

  @staticmethod
  def float_to_bytes(data):
    """Returns a string representation of the given floating point number.
    """
    return '%s ' % data.__repr__()

  @staticmethod
  def bytes_to_float(data):
    remainder, value = Primitives._space_terminated_value(data)
    if value is not None:
      value = float(value)
    return remainder, value

  @staticmethod
  def string_to_bytes(data):
    return '%d %s' % (len(data), data)

  @staticmethod
  def bytes_to_string(data):
    sep_index = data.find(' ')
    if sep_index >= 0:
      str_length = int(data[:sep_index])
      if len(data) > (sep_index + str_length):
        str_start = sep_index + 1
        str_end = str_start + str_length
        value = data[str_start:str_end]
        return data[str_end:], value
    return data, None

  class _PrimitiveSerializer:
    def __init__(self, value):
      self._value = value

    def get_bytes(self):
      if self._value is not None:
        data = self._to_bytes()
        self._value = None
        return data
      return ''

  class _PrimitiveDeserializer:
    def put_bytes(self, data):
      remainder, value = self._from_bytes(data)
      if value is None:
        return remainder, connector.Deserializer.WAIT_FOR_MORE
      else:
        return remainder, connector.Deserializer.DONE, value

  class _BoolSerializer(_PrimitiveSerializer):
    def _to_bytes(self):
      return Primitives.bool_to_bytes(self._value)
  class _BoolDeserializer(_PrimitiveDeserializer):
    def _from_bytes(self, data):
      return Primitives.bytes_to_bool(data)

  class _IntSerializer(_PrimitiveSerializer):
    def _to_bytes(self):
      return Primitives.int_to_bytes(self._value)
  class _IntDeserializer(_PrimitiveDeserializer):
    def _from_bytes(self, data):
      return Primitives.bytes_to_int(data)

  class _FloatSerializer(_PrimitiveSerializer):
    def _to_bytes(self):
      return Primitives.float_to_bytes(self._value)
  class _FloatDeserializer(_PrimitiveDeserializer):
    def _from_bytes(self, data):
      return Primitives.bytes_to_float(data)

  class _StringSerializer(_PrimitiveSerializer):
    def _to_bytes(self):
      return Primitives.string_to_bytes(self._value)
  class _StringDeserializer(_PrimitiveDeserializer):
    def _from_bytes(self, data):
      return Primitives.bytes_to_string(data)

  class _PrimitiveConverter:
    def __init__(self, serializer_class, deserializer_class):
      self._serializer_class = serializer_class
      self._deserializer = deserializer_class()

    def get_serializer(self, data):
      return self._serializer_class(data)

    def get_deserializer(self):
      return self._deserializer

  """Returns a converter that works for booleans.
  """
  BOOL_CONVERTER = _PrimitiveConverter(_BoolSerializer, _BoolDeserializer)
  """Returns a converter that works for integers.
  """
  INT_CONVERTER = _PrimitiveConverter(_IntSerializer, _IntDeserializer)
  """Returns a converter that works for floating point numbers.
  """
  FLOAT_CONVERTER = _PrimitiveConverter(_FloatSerializer, _FloatDeserializer)
  """Returns a converter that works for strings.
  """
  STRING_CONVERTER = _PrimitiveConverter(_StringSerializer, _StringDeserializer)


class _FileStreamSerializer(connector.StreamSerializer):
  class FileReader(threading.Thread):
    def __init__(self, pathname, read_size=1048576):
      threading.Thread.__init__(self)
      self._file = open(pathname, 'rb')
      self._read_size = read_size
      self._data = None
      self._done = False
      self._callback = None
      self._cv = threading.Condition()

      # TODO: set daemon
      self.start()

    def get_bytes(self, callback):
      self._cv.acquire()
      if self._done:
        # Thread has provided the last data and has so terminated.
        read_bytes = self._data if self._data else ''
        self._data = None
      elif self._data:
        # Thread has provided data and is idle and blocked on wait.
        read_bytes = self._data
        self._data = None
        # Wake the thread blocked on wait.
        self._cv.notify()
      else:
        read_bytes = None
        # Thread is busy reading data, so leave the callback here.
        self._callback = callback
      self._cv.release()
      return read_bytes

    def run(self):
      done = False
      while not done:
        # Read more data from the file.
        data = self._file.read(self._read_size)
        done = len(data) < self._read_size
        # If done reading then close the file.
        if done:
          self._file.close()

        self._cv.acquire()
        # Put the data in a shared variable.
        self._data = data
        self._done = done
        # Use the callback to notify that the data is now available.
        if self._callback:
          callback = self._callback
          self._callback = None
          callback()
        # Only wake when the last data put has been taken.
        while self._data and not self._done:
          self._cv.wait()
        self._cv.release()

  def __init__(self, pathname):
    threading.Thread.__init__(self)
    self._reader = FileReader(pathname)

  def get_bytes(self, callback):
    return self._reader.get_bytes(callback)


class _FileStreamDeserializer(connector.StreamDeserializer):
  class FileWriter(threading.Thread):
    def __init__(self, pathname, file_size, write_size=1048576):
      threading.Thread.__init__(self)
      self._file = open(pathname, 'wb')
      self._bytes_remaining = file_size
      self._write_size = write_size
      self._next_data = collections.deque()
      self._next_data_size = 0
      self._data = None
      self._done = False
      self._callback = None
      self._cv = threading.Condition()

      # TODO: set daemon
      self.start()

    def _fill_next_data(self, source):
      # Don't read more data than allowed.
      max_bytes_to_read = min(self._write_size - self._next_data_size, self._bytes_remaining)
      bytes_readable = min(len(source), max_bytes_to_read)

      if bytes_readable:
        self._next_data.append(source[:bytes_readable])
        self._next_data_size += bytes_readable
        remainder = source[bytes_readable:]
        self._bytes_remaining -= bytes_readable
      else:
        remainder = source

      next_data_full = bytes_readable == max_bytes_to_read
      return remainder, next_data_full

    def put_bytes(self, data, callback):
      if self._done:
        return data, connector.StreamDeserializer.DONE

      remainder, next_data_full = self._fill_next_data(data)

      self._cv.acquire()
      if not self._bytes_remaining:
        # The bytes in _next_data are the last data to write.
        if self._data is None:
          # Thread is idle and waiting for more data.
          self._data = ''.join(s for s in self._next_data)
          self._next_data.clear()
          self._next_data_size = 0
          self._done = True
          # Invoke the callback when the last data is written.
          self._callback = callback
          self._cv.notify()
          status = connector.StreamDeserializer.WAIT_FOR_CALLBACK
        else:
          # Thread has not even written the last data left.
          self._callback = callback
          status = connector.StreamDeserializer.WAIT_FOR_CALLBACK
      elif self._write_size == self._next_data_size:
        # Enough bytes accumulated in _next_data to write.
        if self._data is None:
          # Thread is idle and waiting for more data.
          self._data = ''.join(s for s in self._next_data)
          self._next_data.clear()
          self._next_data_size = 0

          # Aggressively try to fill _next_data.
          remainder, next_data_full = self._fill_next_data(remainder)
          if next_data_full:
            # We can write _next_data as soon as _data is written.
            self._callback = callback
            status = connector.StreamDeserializer.WAIT_FOR_CALLBACK
          else:
            # Still need more data to fill _next_data.
            status = connector.StreamDeserializer.WAIT_FOR_MORE

          self._cv.notify()
        else:
          # Thread has not even written the last data left.
          self._callback = callback
          status = connector.StreamDeserializer.WAIT_FOR_CALLBACK
      else:
        # Still need more data to fill _next_data.
        status = connector.StreamDeserializer.WAIT_FOR_MORE

      self._cv.release()
      return remainder, status

    def run(self):
      done = False
      while not done:
        self._cv.acquire()
        while self._data is None:
          self._cv.wait()
        # Copy out the variables to use after releasing the lock.
        data = self._data
        self._data = None
        callback = self._callback
        self._callback = None
        done = self._done
        self._cv.release()

        # Write the stream and quit or callback if neccessary.
        if data:
          self._file.write(data)
        if done:
          self._file.close()
        if callback:
          callback()

  def __init__(self, file_size, pathname):
    threading.Thread.__init__(self)
    self._writer = FileWriter(pathname)

  def put_bytes(self, data, callback):
    return self._writer.put_bytes(data, callback)


class _FileSerializer(connector.Serializer):
  def __init__(self, path_name):
    self._path_name = path_name
    filename = os.path.basename(path_name)
    self._filename_serializer = _StringSerializer(filename)
    self._next_serializer = self._serialize_filename

  def _serialize_filename(self):
    s = self._filename_serializer.get_bytes()
    self._filename_serializer = None
    file_size = os.path.getsize(self._path_name)
    self._file_size_serializer = _StringSerializer(str(file_size))
    self._next_serializer = self._serialize_file_size
    return s

  def _serialize_file_size(self):
    s = self._file_size_serializer.get_bytes()
    self._file_size_serializer = None
    self._next_serializer = self._serialize_file
    return s

  def _serialize_file(self):
    file_serializer = _FileStreamDeserializer(self._path_name)
    self._next_serializer = None

  def get_bytes(self):
    if self._next_deserializer:
      return self._next_deserializer.get_bytes()
    return ''

class _FileDeserializer(connector.Deserializer):
  def __init__(self, save_directory):
    self._save_directory = save_directory
    self._filename_deserializer = _StringDeserializer()
    self._next_deserializer = self._deserialize_filename

  def _choose_filename(self, filename):
    full_pathname = os.path.join(self._save_directory, filename)
    if not os.path.exists(full_pathname):
      return filename, full_pathname

    num = 1
    while True:
      alternate_filename = '%s_(%d)' % (filename, num)
      full_pathname = os.path.join(self._save_directory, alternate_filename)
      if not os.path.exists(full_pathname):
        return alternate_filename, full_pathname
      num += 1

  def _deserialize_filename(self, data):
    values = self._filename_deserializer.put_bytes(data)
    data, status = values[0], values[1]
    if status == connector.Deserializer.WAIT_FOR_MORE:
      # Haven't deserialized the filename yet.
      return data, False
    else:
      # Can deserialize the filename.
      self._filename, self._pathname = self._choose_filename(values[2])
      # Prepare for the next deserialization step.
      self._next_deserializer = self._deserialize_file_size
      return data, True

  def _deserialize_file_size(self, data):
    values = self._filename_deserializer.put_bytes(data)
    data, status = values[0], values[1]
    if status == connector.Deserializer.WAIT_FOR_MORE:
      # Haven't deserialized the file size yet.
      return data, False
    else:
      # Can deserialize the file size.
      self._file_size = int(values[2])
      # Prepare for deserializing the file content next.
      file_stream_deserializer = _FileStreamDeserializer(self._pathname, self._file_size)
      # After finishing writing to the stream, no further deserialization steps.
      self._next_deserializer = None
      return data, file_stream_deserializer

  def put_bytes(self, data):
    while self._next_deserializer:
      data, success = self._next_deserializer(data)
      if type(success) is bool:
        if not success:
          return data, connector.Deserializer.WAIT_FOR_MORE
      else:
        return data, connector.Deserializer.WRITE_TO_STREAM
    return data, connector.Deserializer.DONE, self._filename

class FileConverter(connector.Converter):
  """Converts between files and a wire format where the file content is
  prepended with its name and length."""

  def __init__(self, save_directory):
    self._save_directory = save_directory

  def get_serializer(self, data):
    return _StringSerializer(data)

  def get_deserializer(self):
    return _StringDeserializer(self._save_directory)

