import connector
import converters
import socket
import tornado_connector
import unittest

class TestConnector(unittest.TestCase):
  _SOCKET_CONNECT_TIMEOUT = 0.1
  _BYTES_PER_RECV = 4096

  @classmethod
  def setUpClass(cls):
    cls._listen_port = 33943

  def tearDown(self):
    # The next test method will use a different listening port.
    TestConnector._listen_port += 9

  def _get_connector(self, listening=True):
    converter = converters.Primitives.STRING_CONVERTER
    port = TestConnector._listen_port if listening else None
    return tornado_connector.TornadoConnector(converter, port)

  def _connect_socket(self):
    s = socket.create_connection(('localhost', TestConnector._listen_port),
                                 TestConnector._SOCKET_CONNECT_TIMEOUT)
    return s

  def test_connector_not_listening(self):
    c = self._get_connector(listening=False)
    c.run(block=False)

    # Socket should not connect if not listening.
    try:
      s = self._connect_socket()
      self.fail('should not connect to server not listening')
    except socket.error, msg:
      pass
    c.shutdown()

  def test_connector_listening(self):
    c = self._get_connector(listening=True)
    c.run(block=False)

    # Open a socket to the connector.
    s = self._connect_socket()
    msg = c.recv()
    self.assertEqual(connector.Event.ENDPOINT_CONNECT, msg.type())
    msg.finish_accept(separate=False)

    # Shutdown the connector; this creates a disconnect message.
    c.shutdown()
    msg = c.recv()
    self.assertEqual(connector.Event.FINISH_DISCONNECT, msg.type())

    # The socket should now be disconnected.
    self.assertEqual(s.recv(TestConnector._BYTES_PER_RECV), '')

  def test_connect_not_listening(self):
    c = self._get_connector(listening=False)
    c.run(block=False)

    # Connector should not connect if not listening.
    c.connect('localhost', TestConnector._listen_port)
    msg = c.recv()
    self.assertEqual(msg.type(), connector.Event.FINISH_CONNECT)
    self.assertFalse(msg.success())

    c.shutdown()

  def test_connect_listening(self):
    c = self._get_connector(listening=False)
    c.run(block=False)

    # Listen for the connector.
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.settimeout(TestConnector._SOCKET_CONNECT_TIMEOUT)
    listener.bind(('', TestConnector._listen_port))
    listener.listen(1)

    # The listening socket should accept a connection from the connector.
    c.connect('', TestConnector._listen_port)
    s, addr = listener.accept()
    # The connector should finish connecting.
    msg = c.recv()
    self.assertEqual(msg.type(), connector.Event.FINISH_CONNECT)
    self.assertTrue(msg.success())
    connection = msg.connection()

    # Disconnect from the accepted socket.
    c.disconnect(connection)
    msg = c.recv()
    self.assertEqual(msg.type(), connector.Event.FINISH_DISCONNECT)
    self.assertIsNone(msg.incomplete())
    self.assertFalse(msg.queued())

    # The accepted socket should now be disconnected.
    self.assertEqual(s.recv(TestConnector._BYTES_PER_RECV), '')
    listener.close()

    c.shutdown()

  def test_connect_waiting_not_listening(self):
    c = self._get_connector(listening=False)
    c.run(block=False)

    # Connector should not connect if not listening.
    connection, success = c.connect_waiting(
        'localhost', TestConnector._listen_port)
    self.assertFalse(success)

    c.shutdown()

  def test_connect_waiting(self):
    c = self._get_connector(listening=False)
    c.run(block=False)

    # Listen for the connector.
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(('', TestConnector._listen_port))
    listener.listen(1)

    # The connector should create a half-open connection to the listening socket.
    connection, success = c.connect_waiting(
        '', TestConnector._listen_port)
    self.assertTrue(success)

    # Accept the connection so now a full connection.
    s, addr = listener.accept()

    # Disconnect from the accepted socket.
    c.disconnect(connection)
    msg = c.recv()
    self.assertEqual(msg.type(), connector.Event.FINISH_DISCONNECT)
    self.assertIsNone(msg.incomplete())
    self.assertFalse(msg.queued())

    # The accepted socket should now be disconnected.
    self.assertEqual(s.recv(TestConnector._BYTES_PER_RECV), '')
    listener.close()

    c.shutdown()

  def test_recv(self):
    c = self._get_connector(listening=True)
    c.run(block=False)

    # Open a socket to the connector.
    s = self._connect_socket()
    msg = c.recv()
    msg.finish_accept(separate=False)
    
    # Send three strings to the connector and then close the socket.
    values = ('plango', '', 'hi mom!')
    for value in values:
      serialized = converters.Primitives.string_to_bytes(value)
      s.send(serialized)
    s.close()
    
    # Assert the three strings were received, followed by a disconnect.
    for value in values:
      msg = c.recv()
      self.assertEqual(msg.type(), connector.Event.MESSAGE)
      self.assertEqual(msg.message(), value)
    msg = c.recv()
    self.assertEqual(msg.type(), connector.Event.ENDPOINT_DISCONNECT)

    c.shutdown()

  def _read_until_closed(self, s):
    """Returns a string of all bytes read from the socket until it is closed.
    """
    all_recv_bytes = []
    while True:
      recv_bytes = s.recv(TestConnector._BYTES_PER_RECV)
      if not recv_bytes:
        break
      all_recv_bytes.append(recv_bytes)
    return ''.join(all_recv_bytes)

  def test_send(self):
    c = self._get_connector(listening=True)
    c.run(block=False)

    # Open a socket to the connector.
    s = self._connect_socket()
    msg = c.recv()
    msg.finish_accept(separate=False)
    connection = msg.connection()

    # Send three strings from the connector and then close the connection.
    values = ('plango', '', 'hi mom!')
    for value in values:
      msg = c.send(connection, value)
    c.disconnect(connection)

    # Assert three strings were sent, followed by a disconnect.
    all_recv_bytes = self._read_until_closed(s)
    expected_recv_bytes = ''.join(
        converters.Primitives.string_to_bytes(value) for value in values)
    self.assertEqual(all_recv_bytes, expected_recv_bytes)

    c.shutdown()

  class _PausingStream:
    """Stream that returns WAIT_FOR_CALLBACK on the first call to get_bytes, and
    then returns its value before ending.
    """
    def __init__(self, value):
      self._value = value
      self._event = threading.Event()
      self._get_next_bytes = self._set_callback

    def get_bytes(self, callback):
      return self._get_next_bytes(callback)

    def _set_callback(self, callback):
      self._callback = callback
      self._event.set()
      return connector.StreamSerializer.WAIT_FOR_CALLBACK

    def _wait_for_callback(self):
      self._event.wait()

    def _use_callback(self):
      self._get_next_bytes = self._get_value
      self._callback()

    def _get_value(self):
      self._get_next_bytes = self._get_done
      return self._value

    def _get_done(self):
      return ''

  class _PausingSerializer:
    """Returns the string 'abc', a _PausingStream that returns its value, and
    finally the string '123' before ending.
    """
    def __init__(self, value):
      self._stream = _PausingStream(value)
      self._get_next_bytes = self._get_start_bytes

    def _get_start_bytes(self):
      self._get_next_bytes = self._get_pausing_stream
      return 'abc'

    def _get_pausing_stream(self):
      self._get_next_bytes = self._get_last_stream
      return self._pausing_stream

    def _get_last_bytes(self):
      self._get_next_bytes = self._get_done
      return '123'

    def _get_done(self):
      return ''

    def get_bytes(self):
      return self._get_next_bytes()

  class _PausingConverter:
    def get_serializer(self, data):
      return _PausingSerializer(data)
    
    def get_deserializer(self):
      pass

  @unittest.skip('')
  def test_disconnect_now(self):
    converter = _PausingConverter()
    connector = self._get_connector(listening=True, converter=converter)
    connector.run()

    # Listen for the connector.
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(('', TestConnector._LISTEN_PORT))
    listener.listen(1)

    # The connector should create a half-open connection to the listening socket.
    connection, success = connector.connect_waiting(
        '', TestConnector._LISTEN_PORT)
    self.assertTrue(success)
    # Accept the connection so now a full connection.
    s, addr = listener.accept()

    # Wait for the first message to be partially serialized.
    connector.send(connection, 'ONE')
    converter._wait_for_callback()
    # Enqueue the second message and disconnect.
    connector.send(connection, 'TWO')
    connector.disconnect(connection, now=True)
    # Continue serializing the first message.
    converter._use_callback()

    # The first message is sent incomplete, the second is queued.
    msg = self.recv()
    self.assertEqual(msg.type(), connector.Event.FINISH_DISCONNECT)
    self.assertEqual('ONE', msg.incomplete())
    self.assertEqual(['TWO'], msg.queued())

    # The first message is received incomplete.
    all_recv_bytes = self._read_until_closed(s)
    self.assertEqual(all_recv_bytes, 'abc')

    connector.shutdown()

  @unittest.skip('')
  def test_disconnect_finish_message(self):
    converter = _PausingConverter()
    connector = self._get_connector(listening=True, converter=converter)
    connector.run()

    # Listen for the connector.
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(('', TestConnector._LISTEN_PORT))
    listener.listen(1)

    # The connector should create a half-open connection to the listening socket.
    connection, success = connector.connect_waiting(
        '', TestConnector._LISTEN_PORT)
    self.assertTrue(success)
    # Accept the connection so now a full connection.
    s, addr = listener.accept()

    # Wait for the first message to be partially serialized.
    connector.send(connection, 'ONE')
    converter._wait_for_callback()
    # Enqueue the second message and disconnect.
    connector.send(connection, 'TWO')
    connector.disconnect(connection, now=True)
    # Continue serializing the first message.
    converter._use_callback()

    # The first message is sent completely, the second is queued.
    msg = self.recv()
    self.assertEqual(msg.type(), connector.Event.FINISH_DISCONNECT)
    self.assertIsNone(msg.incomplete())
    self.assertEqual(['TWO'], msg.queued())

    # The first message is received completely.
    all_recv_bytes = self._read_until_closed(s)
    self.assertEqual(all_recv_bytes, 'abcONE123')

    connector.shutdown()

  @unittest.skip('')
  def test_disconnect_finish_queue(self):
    converter = _PausingConverter()
    connector = self._get_connector(listening=True, converter=converter)
    connector.run()

    # Listen for the connector.
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(('', TestConnector._LISTEN_PORT))
    listener.listen(1)

    # The connector should create a half-open connection to the listening socket.
    connection, success = connector.connect_waiting(
        '', TestConnector._LISTEN_PORT)
    self.assertTrue(success)
    # Accept the connection so now a full connection.
    s, addr = listener.accept()

    # Wait for the first message to be partially serialized.
    connector.send(connection, 'ONE')
    converter._wait_for_callback()
    # Enqueue the second message and disconnect.
    connector.send(connection, 'TWO')
    connector.disconnect(connection, now=True)
    # Continue serializing the first message.
    converter._use_callback()

    # Serialize the second message.
    converter._wait_for_callback()
    converter._use_callback()

    # Both messages are sent completely.
    msg = self.recv()
    self.assertEqual(msg.type(), connector.Event.FINISH_DISCONNECT)
    self.assertIsNone(msg.incomplete())
    self.assertFalse(msg.queued())

    # Both messages are received completely.
    all_recv_bytes = self._read_until_closed(s)
    self.assertEqual(all_recv_bytes, 'abcONE123abcTWO123')

    connector.shutdown()

  def test_recv_connection(self):
    c = self._get_connector(listening=True)
    c.run(block=False)

    # Open a socket to the connector.
    s = self._connect_socket()
    msg = c.recv()
    connection = msg.connection()
    msg.finish_accept(separate=True)
    
    # Send two strings to the connector and then close the socket.
    values = ('foo', 'bar')
    for value in values:
      serialized = converters.Primitives.string_to_bytes(value)
      s.send(serialized)
    s.close()
    
    # Assert the two strings were received, followed by a disconnect.
    for value in values:
      msg = c.recv_connection(connection)
      self.assertEqual(msg.type(), connector.Event.MESSAGE)
      self.assertEqual(msg.message(), value)
    msg = c.recv_connection(connection)
    self.assertEqual(msg.type(), connector.Event.ENDPOINT_DISCONNECT)

    c.shutdown()

  def test_merge_connection(self):
    c = self._get_connector(listening=True)
    c.run(block=False)

    # Open a socket to the connector.
    s = self._connect_socket()
    msg = c.recv()
    connection = msg.connection()
    msg.finish_accept(separate=True)

    # Send two strings to the connector and then close the socket.
    values = ('foo', 'bar')
    for value in values:
      serialized = converters.Primitives.string_to_bytes(value)
      s.send(serialized)
    s.close()
 
    # Receive the first string, and then merge the connection.
    msg = c.recv_connection(connection)
    self.assertEqual(msg.type(), connector.Event.MESSAGE)
    self.assertEqual(msg.message(), 'foo')
    c.merge_connection(connection)

    # Receive the second string, followed by a disconnect.
    msg = c.recv_connection(connection)
    self.assertEqual(msg.type(), connector.Event.MESSAGE)
    self.assertEqual(msg.message(), 'bar')
    msg = c.recv_connection(connection)
    self.assertEqual(msg.type(), connector.Event.ENDPOINT_DISCONNECT)

    c.shutdown()

  @unittest.skip('')
  def test_connect_separate(self):
    pass

  @unittest.skip('')
  def test_connect_waiting_with_callback(self):
    pass

  @unittest.skip('')
  def test_shutdown_now(self):
    pass

  @unittest.skip('')
  def test_shutdown_finish_messages(self):
    pass

  @unittest.skip('')
  def test_shutdown_finish_queues(self):
    pass

