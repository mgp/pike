import collections
import connector
import errno
import fcntl
import logging
import socket
import thread
import threading

import tornado_io_loop
import tornado_stack_context

class _Queue:
  """Our own queue implementation so that we can retrieve multiple elements
  while holding the lock.
  """

  def __init__(self, deque=None):
    self._condition = threading.Condition()
    self._deque = collections.deque() if deque is None else deque

  def put(self, element):
    self._condition.acquire()
    self._deque.append(element)
    self._condition.notify()
    self._condition.release()

  def get(self):
    self._condition.acquire()
    while not self._deque:
      self._condition.wait()
    element = self._deque.popleft()
    self._condition.release()
    return element

  def extend(self, iterable):
    self._condition.acquire()
    self._deque.extend(iterable)
    self._condition.release()

  def clear_for_merge(self):
    """This queue is separated but is now merging into the general queue. The
    network thread will not append any more messages to this queue."""
    self._condition.acquire()
    d = self._deque
    self._deque = None
    # TODO: Wake up any waiting threads.
    self._condition.release()
    return d

class _Event:
  """Base class for all events that are run as callbacks in the network
  thread.
  """

  def __init__(self, connector):
    self._connector = connector

class _FinishAcceptEvent(_Event):
  """Event for when the application uses the finish_accept callback for a
  RemoteConnectEvent.
  """

  def __init__(self, connector, connection, separate):
    _Event.__init__(self, connector)    
    self._connection = connection
    self._separate = separate

  def run(self):
    self._connector._finish_accept(self._connection, self._separate)

class _ConnectEvent(_Event):
  """Event for when the application connects to an endpoint asynchronously.
  """

  def __init__(self, connector, connection):
    _Event.__init__(self, connector)    
    self._connection = connection

  def run(self):
    self._connector._connect(self._connection)

class _ConnectWaitingEvent(_Event):
  """Event for when the application connects to an endpoint synchronously.
  """

  def __init__(self, connector, connection):
    _Event.__init__(self, connector)    
    self._connection = connection
    self._finished = False
    self._connected = False
    self._proceeded = False
    self._cv = threading.Condition()

  def run(self):
    self._connector._connect_waiting(self)

  def finish(self, connected):
    self._cv.acquire()
    proceeded = self._proceeded
    if not proceeded:
      self._finished = True
      self._connected = connected
      self._cv.notify()
    self._cv.release()
    return proceeded

class _ConnectedWaitingEvent(_Event):
  """Event for when the callback has run for a connection that the application is
  waiting synchronously for.
  """

  def __init__(self, connector, connection, separate):
    _Event.__init__(self, connector)    
    self._connection = connection
    self._separate = separate

  def run(self):
    self._connector._connected_waiting(self._connection, self._separate)

class _DisconnectEvent(_Event):
  """Event for when the application disconnects from an endpoint.
  """

  def __init__(self, connector, connection, now, finish_message, finish_queue):
    _Event.__init__(self, connector)    
    self._connection = connection
    self._now = now
    self._finish_message = finish_message
    self._finish_queue = finish_queue

  def run(self):
    self._connector._disconnect(self._connection, self._now, self._finish_message, self._finish_queue)

class _MessageEvent(_Event):
  """Event for when the application sends a message to an endpoint.
  """

  def __init__(self, connector, connection, data):
    _Event.__init__(self, connector)    
    self._connection = connection
    self._data = data

  def run(self):
    self._connector._send_message(self._connection, self._data)

class _MergeConnectionEvent(_Event):
  """Event for when the messages of a connection should be merged into the
  general queue."""

  def __init__(self, connector, connection):
    _Event.__init__(self, connector)
    self._connection = connection

  def run(self):
    self._connector._merge_connection(self._connection)

class _ReadCallbackCalledEvent(_Event):
  """Event for when a stream invokes a callback signaling that it is ready for
  reading.
  """

  def __init__(self, connector, dispatcher):
    _Event.__init__(self, connector)    
    self._dispatcher = dispatcher

  def run(self):
    self._connector._enable_reads(self._dispatcher)

class _WriteCallbackCalledEvent(_Event):
  """Event for when a stream invokes a callback signaling that it is ready for
  writing.
  """

  def __init__(self, connector, dispatcher):
    _Event.__init__(self, connector)    
    self._dispatcher = dispatcher

  def run(self):
    self._connector._enable_writes(self._dispatcher)

class _ShutdownEvent(_Event):
  """Event for shutting down the connector.
  """

  def __init__(self, connector, now, finish_messages, finish_queues):
    _Event.__init__(self, connector)    
    self._now = now
    self._finish_messages = finish_messages
    self._finish_queues = finish_queues
    self._e = threading.Event()

  def run(self):
    self._connector._shutdown(self)

  def finished(self):
    self._e.set()


class _Listener:
  """A listener for new connections from endpoints."""

  def __init__(self, connector, port, address=""):
    self._connector = connector

    # Bind the socket.
    self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM, 0)
    flags = fcntl.fcntl(self._socket.fileno(), fcntl.F_GETFD)
    flags |= fcntl.FD_CLOEXEC
    fcntl.fcntl(self._socket.fileno(), fcntl.F_SETFD, flags)
    self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    self._socket.setblocking(0)
    self._socket.bind((address, port))
    self._socket.listen(128)

    # Begin listening for new connections.
    self._connector._io_loop.add_handler(
        self._socket.fileno(),
        self._handle_events,
        self._connector._io_loop.READ)

  def _handle_events(self, fd, events):
    while True:
      try:
        new_socket, address = self._socket.accept()
      except socket.error, e:
        if e.args[0] in (errno.EWOULDBLOCK, errno.EAGAIN):
          return
        raise

      self._connector._endpoint_connected(new_socket, address)

  def _shutdown(self):
    self._connector._io_loop.remove_handler(self._socket.fileno())
    self._socket.close()


class _Endpoint:
  """An endpoint to which the application has established or is establishing a
  connection. This class encapsulates all state for sending and receiving data
  with the endpoint, and connecting or disconnecting with the endpoint."""

  BYTES_PER_RECV = 4096
  MAX_READ_BUFFER_SIZE = 256 * 1024
  MAX_WRITE_BUFFER_SIZE = 256 * 1024

  # The connection is opening or already open.
  NOT_DISCONNECTING = 0
  # The application is disconnecting after the incomplete message sends.
  FINISH_MESSAGE = 1
  # The application is disconnecting after the queue empies.
  FINISH_QUEUE = 2
  # The last data was sent and the application is disconnected.
  DISCONNECTED = 3
  # The endpoint disconnected, but the read buffer may still be emptying.
  ENDPOINT_DISCONNECTED = 4

  def _add_as_handler(self):
    """Adds this _Endpoint as a handler for events on its socket."""
    # Register with the io_loop to participate in events.
    with tornado_stack_context.NullContext():
      self._connector._io_loop.add_handler(
          self._socket.fileno(), self._handle_events, self._state)

  def __init__(self, connector, s=None):
    self._connector = connector
    self._shutting_down = False

    # Whether the connector is connecting to the endpoint.
    self._connecting = False
    self._connected_callback = None
    # Whether the connector is disconnecting from the endpoint.
    self._disconnect_status = _Endpoint.NOT_DISCONNECTING

    # Queue of data not yet fully serialized.
    self._queue = collections.deque()
    # Queue of objects serialized in buffer, paired with their size.
    self._in_buffer = collections.deque()
    self._in_buffer_progress = 0
    # The buffer containing serialized objects.
    self._write_buffer = collections.deque()
    self._write_buffer_size = 0
    # Serializer for the object at the head of the queue.
    self._serializer = None
    self._serializer_progress = 0
    # Stream returned by the last call to the serializer.
    self._write_stream = None
    # Whether the connector is waiting for the stream to use the callback.
    self._waiting_write_callback = False
    self._write_callback = None

    # The buffer containing bytes to deserialize.
    self._read_buffer = collections.deque()
    self._read_buffer_size = 0
    # Deserializer for the read buffer.
    self._deserializer = None
    # Stream returned by the last call to the deserializer.
    self._read_stream = None
    # Whether the connector is waiting for the stream to use the callback.
    self._waiting_read_callback = False
    self._read_callback = None

    self._socket = s
    self._state = self._connector._io_loop.ERROR
    if self._socket is not None:
      self._socket.setblocking(False)
      # The socket is from a call to accept and ready for reading.
      self._state |= self._connector._io_loop.READ
      self._add_as_handler()

  def _connect(self, address, connected_callback=None):
    """Connects the socket to a remote address without blocking.

    May only be called if the socket passed to the constructor was not
    previously connected.
    """
    self._connecting = True
    self._connected_callback = connected_callback

    self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    self._socket.setblocking(False)
    try:
      self._socket.connect(address)
    except socket.error, e:
      # In non-blocking mode connect() always raises an exception.
      if e.args[0] not in (errno.EINPROGRESS, errno.EWOULDBLOCK):
        raise

    # The connection is completed on a write event.
    self._state |= self._connector._io_loop.WRITE
    self._add_as_handler()

  def _enqueue(self, data):
    """Enqueues the given message for writing to the socket."""
    if self._disconnect_status != _Endpoint.NOT_DISCONNECTING:
      # Cannot enqueue new data if disconnecting.
      # TODO: Put on a queue that will not be read, but appended to unsent.
      return

    self._queue.append(data)
    if not self._write_callback:
      self._add_io_state(self._connector._io_loop.WRITE)

  def _get_unsent(self):
    """Returns the message not completely sent, and messages not sent at all,
    even if already serialized in the write buffer."""
    incomplete = None
    unsent = []
    if len(self._in_buffer):
      head, head_size = self._in_buffer[0]
      # in_progress stores how many bytes we have sent of the first messge in buffer.
      if self._in_buffer_progress:
        incomplete = head
        self._in_buffer.popleft()
      unsent.extend((data for (data, data_size) in self._in_buffer))
    unsent.extend((data for data in self._queue))

    return incomplete, unsent

  def _finish_disconnect(self):
    """Sends the FINISH_DISCONNECT event to the application."""
    incomplete, unsent = self._get_unsent()
    self._connector._finish_disconnect(self, incomplete, unsent)

  def _disconnect(self, now, finish_message, finish_queue):
    """Begins disconnecting from the endpoint after sending whatever remaining
    messages were specified."""
    if now:
      self.close()
      self._finish_disconnect()
    elif finish_message:
      self._disconnect_status = _Endpoint.FINISH_MESSAGE
      if not self._write_buffer and not self._serializer:
        # Not serializing a message, so disconnect now.
        self.close()
        self._finish_disconnect()
    else:
      self._disconnect_status = _Endpoint.FINISH_QUEUE
      if not self._write_buffer and not self._serializer and not self._queue:
        # Not serializing a message and queue is empty, so disconnect now.
        self.close()
        self._finish_disconnect()

  def close(self):
    """Close this stream."""
    if self._socket is not None:
      self._connector._io_loop.remove_handler(self._socket.fileno())
      self._socket.close()
      self._socket = None

  def _read_buffer_not_full(self):
    """Returns true if there is room in the read buffer for data read from the
    socket."""
    return self._read_buffer_size < _Endpoint.MAX_READ_BUFFER_SIZE

  def _has_data_to_write(self):
    """Returns true if there is data in the write buffer to be written to the
    socket, or the write buffer can be filled with data."""
    return (self._write_buffer_size or
            # Serializing a stream.
            (self._write_stream and not self._write_stream_callback) or
            # Serializing a message.
            self._serializer or
            # Can begin serializing a message.
            self._queue)

  def _handle_error(self):
    # TODO
    self.close()

  def _handle_events(self, fd, events):
    """Acts on the given events for the socket."""
    if not self._socket:
      logging.warning("Got events for closed stream %d", fd)
      return
    try:
      # Act on events generated by the current state.
      if events & self._connector._io_loop.READ:
        self._handle_read()
      if not self._socket:
        return
      has_error = events & self._connector._io_loop.ERROR
      if events & self._connector._io_loop.WRITE:
        if self._connecting:
          # If could not connect to endpoint, ERROR is also set.
          self._handle_connect(not has_error)
          if has_error:
            return
        self._handle_write()
      if not self._socket:
        return
      if has_error:
        self._handle_error()
        return

      # Generate the next state for the next events.
      state = self._connector._io_loop.ERROR
      if self._read_buffer_not_full():
        state |= self._connector._io_loop.READ
      if self._has_data_to_write():
        state |= self._connector._io_loop.WRITE
      if state != self._state:
        self._state = state
        self._connector._io_loop.update_handler(self._socket.fileno(), self._state)
    except:
      logging.error("Uncaught exception, closing connection.",
                    exc_info=True)
      self.close()
      raise

  def _fill_read_buffer(self):
    """Reads from the socket and appends the contents to the read buffer."""
    while self._read_buffer_size < _Endpoint.MAX_READ_BUFFER_SIZE:
      try:
        chunk = self._socket.recv(_Endpoint.BYTES_PER_RECV)
      except socket.error, e:
        if e.args[0] in (errno.EWOULDBLOCK, errno.EAGAIN):
          # The connection is open but there is nothing more to read. 
          break
        else:
          raise
      if not chunk:
        # The endpoint has closed the connection.
        return False
      else:
        self._read_buffer.append(chunk)
        self._read_buffer_size += len(chunk)
    return True

  def _merge_read_buffer(self):
    """Attempts to merge the contents of the read buffer into a single string if
    the deserializer returned WAIT_FOR_MORE."""
    if len(self._read_buffer) > 1:
      # Merge the contents of the buffer as one string.
      merged = ''.join(self._read_buffer.popleft() for i in xrange(len(self._read_buffer)))
      self._read_buffer.append(merged)
      return True
    return False

  def _leave_only_remainder(self, remainder):
    """Replaces the first element of the read buffer, which was passed to the
    deserializer, with the remainder it returned."""
    if remainder:
      head = self._read_buffer[0]
      if (remainder is head) or (len(remainder) >= len(head)):
        # No bytes were consumed by the deserializer.
        pass
      else:
        self._read_buffer.popleft()
        self._read_buffer.appendleft(remainder)
        self._read_buffer_size -= (len(head) - len(remainder))
    else:
      # No remainder, so the head of read buffer was deserialized.
      head = self._read_buffer.popleft()
      self._read_buffer_size -= len(head)

  def _empty_read_buffer(self):
    """Writes the contents of the read buffer to deserializers until the read
    buffer is empty or writing is blocked on a callback."""
    while self._read_buffer_size and not self._waiting_read_callback:
      # Already writing to a stream.
      if self._read_stream:
        remainder, status = self._read_stream.put_bytes(self._read_buffer[0])
        self._leave_only_remainder(remainder)
        if status == connector.StreamDeserializer.WAIT_FOR_CALLBACK:
          # Application will invoke the callback when it can accept more data.
          self._waiting_read_callback = True
          break
        elif status == connector.StreamDeserializer.WAIT_FOR_MORE:
          if self._merge_read_buffer():
            # Successfully merged; the next call to put_bytes may succeed.
            continue
          else:
            # All the bytes in the read buffer were not sufficient.
            # TODO: problem if endpoint has disconnected and no more data coming
            break
        else:
          # Stream is empty; the deserializer will be used on the next pass.
          self._read_stream = None
          continue

      if not self._deserializer:
        # Not yet writing to a deserializer; create one for pending data.
        self._deserializer = self._connector._get_deserializer()

      # Write to the deserializer.
      v = self._deserializer.put_bytes(self._read_buffer[0])
      remainder, status = v[0], v[1]
      self._leave_only_remainder(remainder)
      if status == connector.Deserializer.WRITE_TO_STREAM:
        # Must write to a stream on the next pass.
        self._read_stream = v[2]
      elif status == connector.Deserializer.WAIT_FOR_MORE:
        if self._merge_read_buffer():
          # Successfully merged; the next call to put_bytes may succeed.
          continue
        else:
          # All the bytes in the read buffer were not sufficient.
          # TODO: problem if endpoint has disconnected and no more data coming
          break
      else:
        # Deserializer completed; forward the value to the application.
        self._deserializer = None
        self._connector._receive_message(self, v[2])

    # The connection is closed and the last data has been deserialized.
    if not self._socket:
      incomplete, unsent = self._get_unsent()
      self._connector._endpoint_disconnected(self, incomplete, unsent)

  def _handle_read(self):
    """Called when the socket is readable."""
    # Fill the read buffer as much as we can, until we reach the threshold.
    still_connected = self._fill_read_buffer()
    if not still_connected:
      # Do not attempt to perform any writes.
      self._connector._io_loop.remove_handler(self._socket.fileno())
      self._socket = None

    # Write the contents of the buffer to the serializers.
    self._empty_read_buffer()

  def _fill_write_buffer(self):
    """Reads from the serializers and appends the contents to the write
    buffer."""
    while not self._waiting_write_callback and (
          self._write_buffer_size < _Endpoint.MAX_WRITE_BUFFER_SIZE):
      if self._write_stream:
        # Read the data from the stream.
        data = self._write_stream.get_bytes(self._write_callback)
        if data == StreamSerializer.WAIT_FOR_CALLBACK:
          # Application will invoke the callback when it has more data.
          self._waiting_write_callback = True
          break
        elif data:
          # The stream returned data and did not block; read again on the next pass.
          self._write_buffer.append(data)
          self._write_buffer_size += len(data)
          self._serializer_progress += len(data)
          continue
        else:
          # Stream is empty; the serializer will be used on the next pass.
          self._write_stream = None

      if not self._serializer:
        if self._disconnect_status == _Endpoint.FINISH_MESSAGE:
          # If we have sent the last message, do not begin deserializing another.
          break
        # Prepare serializing the next enqueued, or exit if queue is empty.
        if self._queue:
          self._serializer = self._connector._get_serializer(self._queue[0])
        else:
          break

      data = self._serializer.get_bytes()
      if type(data) == str:
        if data:
          # The serializer returned data; read again on the next pass.
          self._write_buffer.append(data)
          self._write_buffer_size += len(data)
          self._serializer_progress += len(data)
          continue
        else:
          # Serializer is done; create a new serializer on the next pass.
          self._in_buffer.append((self._queue.popleft(), self._serializer_progress))
          self._serializer = None
          self._serializer_progress = 0
      else:
        # The returned stream will be used on the next pass.
        self._write_stream = data

  def _empty_write_buffer(self):
    """Reads from the write buffer and writes the contents to the socket."""
    # Write as many bytes to the socket as allowed.
    while self._write_buffer:
      try:
        head = self._write_buffer[0]
        num_bytes = self._socket.send(head)
        if num_bytes >= len(head):
          self._write_buffer.popleft()
        elif num_bytes > 0:
          self._write_buffer.popleft()
          self._write_buffer.appendleft(head[num_bytes:])
        else:
          # No more data could be written to the socket.
          break

        self._in_buffer_progress += num_bytes
        self._write_buffer_size -= num_bytes
      except socket.error, e:
        # FIXME: do something better here
        logging.warning("Write error on %d: %s", self._socket.fileno(), e)
        self.close()
        return

    # Remove the objects that have now been written to the socket.
    while self._in_buffer:
      head, head_size = self._in_buffer[0]
      if head_size <= self._in_buffer_progress:
        self._in_buffer_progress -= head_size
        self._in_buffer.popleft()
      else:
        # This object is partially sent at most.
        break

  def _handle_write(self):
    """Called when the socket is writable."""
    # Fill our write buffer as much as we can, until we reach the threshold.
    self._fill_write_buffer()
    # Write the contents of the write buffer to the socket.
    self._empty_write_buffer()

    if not self._write_buffer:
      if (self._disconnect_status == _Endpoint.FINISH_MESSAGE) and not self._deserializer:
        # The last message has been sent, so disconnect.
        self.close()
        self._finish_disconnect()
        return
      elif (self._disconnect_status == _Endpoint.FINISH_QUEUE) and (
          not self._deserializer and not self._queue):
        # The last message in the queue has been sent, so disconnect.
        self.close()
        self._finish_disconnect()
        return

  def _handle_connect(self, success):
    self._connecting = False
    incomplete, unsent = self._get_unsent()
    if self._connected_callback:
      # A callback is provided if connecting with connect_waiting.
      connected_callback = self._connected_callback
      self._connected_callback = None
      self._connector._finish_connect_waiting(self, success, connected_callback)
    else:
      # No callback is provided if connecting with the asynchronous connect.
      self._connector._finish_connect(self, success, unsent)

  def _has_io_state(self, state):
    """Returns whether this connection is interested in the given event."""
    return bool(self._state & state)

  def _add_io_state(self, state):
    """Adds interest for the given event."""
    if self._socket is None:
      # Connection has been closed, so there can be no future events.
      return
    if not self._state & state:
      self._state = self._state | state
      self._connector._io_loop.update_handler(self._socket.fileno(), self._state)

  def _remove_io_state(self, state):
    """Removes interest for the given event."""
    if self._socket is None:
      # Connection has been closed, so there can be no future events.
      return
    if self._state & state:
      self._state = self._state & ~state
      self._connector._io_loop.update_handler(self._socket.fileno(), self._state)

  def stream_read_ready(self):
    """Callback method used when """
    self._read_callback = None
    self._waiting_read_callback = False
    self._endpoint._run_as_callback(_CallbackReadyEvent())

  def stream_write_ready(self):
    """Callback method used when """
    self._write_callback = None
    self._waiting_write_callback = False
    self._endpoint._run_as_callback(_CallbackReadyEvent())


class _TornadoConnection(connector.Connection):
  def __init__(self, address, endpoint=None):
    self._address = address
    self._endpoint = endpoint

  def _address(self):
    return self._address


class TornadoConnector(connector.Connector):
  """An implementation of Connector that uses the level triggered I/O loop from
  the Tornado web server for managing sockets."""

  def __init__(self, converter, port=None):
    self._converter = converter
    self._io_loop = tornado_io_loop.IOLoop()
    self._listener = None if port is None else _Listener(self, port)
    self._shutting_down = False

    # The queue used by recv.
    self._recv_queue = _Queue()
    # Queues for separated connections, or None if using _recv_queue.
    self._conn_queues = {}
    self._conn_queues_lock = threading.Lock()

    # Queues for remotely established connections waiting on the accept callback.
    self._pending_conn_deques = {}

  def _get_serializer(self, data):
    return self._converter.get_serializer(data)

  def _get_deserializer(self):
    return self._converter.get_deserializer()

  def run(self, block=True):
    if block:
      self._io_loop.start()
    else:
      # Another thread is responsible for running the network thread.
      thread.start_new_thread(self._io_loop.start, ())

  def _finish_accept(self, connection, separate):
    """Executed in the network thread when the _FinishAcceptEvent created by the
    _enqueue_finish_accept method is run."""
    d = self._pending_conn_deques.pop(connection, None)
    if d is None:
      # This callback has already been invoked.
      return

    self._conn_queues_lock.acquire()
    try:
      if separate:
        conn_queue = self._conn_queues[connection]
      else:
        # Will not be using the separated queue.
        self._conn_queues[connection] = None
    finally:
      self._conn_queues_lock.release()

    if separate:
      # Populate the connection's separated queue with the received messages.
      conn_queue.extend(d)
    else:
      # Not separated, so populate the main queue with the received messages.
      self._recv_queue.extend(d)

  def _endpoint_disconnected(self, endpoint, incomplete, unsent):
    event = connector.RemoteDisconnectEvent(endpoint._connection, incomplete, unsent)
    self._enqueue_event(endpoint._connection, event)

  def _enqueue_finish_accept(self, connection, separate):
    """Delegated to by the finish_accept method of RemoteConnectEvent to
    complete the operation in the network thread."""
    event = _FinishAcceptEvent(self, connection, separate)
    self._run_as_callback(event)

  def _make_connection_queue(self, connection, separate):
    """Creates a queue for a separated connection."""
    conn_queue = _Queue() if separate else None
    self._conn_queues_lock.acquire()
    try:
      self._conn_queues[connection] = conn_queue
    finally:
      self._conn_queues_lock.release()

  def _endpoint_connected(self, s, address):
    """Called by _Listener when an endpoint has connected through the listening
    port to this connector."""
    endpoint = _Endpoint(self, s)
    connection = _TornadoConnection(address, endpoint)
    endpoint._connection = connection

    # Queue for received messages until the connection is accepted.
    self._pending_conn_deques[connection] = collections.deque()

    # Make a separated queue that may be deleted when finish_accept callback
    # runs, but is needed so recv_connection blocks immediately afterward.
    self._make_connection_queue(connection, True)

    # Notify the application that an endpoint connected.
    event = connector.RemoteConnectEvent(connection, self._enqueue_finish_accept)
    self._recv_queue.put(event)

  def connect(self, host, port, separate=False):
    # Create the separate queue now so the client can call recv_connection immediately.
    address = (host, port)
    connection = _TornadoConnection(address)
    self._make_connection_queue(connection, separate)

    # The ConnectEvent will run before any messages enqueued by the client.
    event = _ConnectEvent(self, connection)
    self._run_as_callback(event)

    return connection

  def connect_waiting(self, host, port, separate=False, callback=None, timeout=10.0):
    # Create the separate queue.
    address = (host, port)
    connection = _TornadoConnection(address)
    self._make_connection_queue(connection, separate)

    # Delegate connecting to the endpoint to the network thread.
    event = _ConnectWaitingEvent(self, connection)
    self._run_as_callback(event)

    event._cv.acquire()
    if not event._finished:
      event._cv.wait(timeout)
    # True if the network thread called notify, False if timeout expired.
    finished = event._finished
    connected = event._connected
    if not finished:
      # Set so the network thread disconnects later.
      event._proceeded = True
    event._cv.release()

    # Invoke the callback if one was provided.
    if callback is not None:
      callback(connection, connected)
    # Now that the callback is invoked, start delivering messages.
    if finished and connected:
      event = _ConnectedWaitingEvent(self, connection, separate)
      self._run_as_callback(event)

    return connection, connected

  def disconnect(self, connection, now=False, finish_message=False, finish_queue=True):
    event = _DisconnectEvent(self, connection, now, finish_message, finish_queue)
    self._run_as_callback(event)

  def send(self, connection, data):
    event = _MessageEvent(self, connection, data)
    self._run_as_callback(event)

  def recv(self):
    return self._recv_queue.get()

  def recv_connection(self, connection):
    conn_queue = None
    with self._conn_queues_lock:
      conn_queue = self._conn_queues.get(connection, None)
    if conn_queue is None:
      return None
    return conn_queue.get()

  def _merge_connection(self, connection):
    self._conn_queues_lock.acquire()
    try:
      conn_queue = self._conn_queues.get(connection)
      # Treated the same if the connection key doesn't exist, or the value is None.
      if conn_queue is not None:
        self._conn_queues[connection] = None
    finally:
      self._conn_queues_lock.release()

    if conn_queue is not None:
      d = conn_queue.clear_for_merge()
      self._recv_queue.extend(d)

  def merge_connection(self, connection):
    # TODO: grab queues lock and set aside?
    event = _MergeConnectionEvent(self, connection)
    self._run_as_callback(event)

  def shutdown(self, now=False, finish_messages=False, finish_queues=True):
    event = _ShutdownEvent(self, now, finish_messages, finish_queues)
    self._run_as_callback(event)

    event._e.wait()

  def _finish_connect(self, endpoint, success, unsent):
    connection = endpoint._connection
    event = connector.FinishConnectEvent(connection, success, unsent)
    self._enqueue_event(connection, event)

    if not success:
      # TODO: Can't delete these, because if shutdown finds _conn_queues it will
      # attempt to disconnect the endpoint of each one.
      """
      # Don't let the client retaining the Connection instance retain the Endpoint.
      del connection._endpoint
      del endpoint._connection
      """

      # TODO: Can't delete _conn_queues[connection] until the client takes the
      # FinishConnectEvent which may be enqueued on it; have the Queue deleted
      # automatically once that event is taken.

      # TODO: what to do if shutting down?

  def _finish_connect_waiting(self, endpoint, success, callback):
    connection = endpoint._connection

    # TODO: refactor into common method once i know it really is common
    if not success:
      # Don't let the client retaining the Connection instance retain the Endpoint.
      del connection._endpoint
      del endpoint._connection

      # Remove all state for the disconnected endpoint.
      with self._conn_queues_lock:
        del self._conn_queues[connection]

      callback(success)
    else:
      # Use the callback to notify the application of connection outcome.
      proceeded = callback(success)
      if proceeded:
        # The application timeout expired and so it has no need for the connection.
        connection.close()

        # Don't let the client retaining the Connection instance retain the Endpoint.
        del connection._endpoint
        del endpoint._connection

        # Remove all state for the disconnected endpoint.
        with self._conn_queues_lock:
          del self._conn_queues[connection]

        # TODO: what to do if shutting down?

  def _enqueue_event(self, connection, event):
    """Appends the given event to the queue for the given connection."""
    queue = self._conn_queues.get(connection, None)
    if queue is not None:
      queue.put(event)
    else:
      self._recv_queue.put(event)

  def _receive_message(self, endpoint, message):
    connection = endpoint._connection
    event = connector.MessageReceivedEvent(connection, message)
    self._enqueue_event(connection, event)

  def _run_as_callback(self, event):
    """Enqueues the given event for running as a callback by the network thread."""
    # Enqueue the event, and then wake from select to read it.
    self._io_loop.add_callback(event.run)

  def _make_connecting_endpoint(self, connection):
    """Returns an _Endpoint for the given connection which is still being
    established."""
    endpoint = _Endpoint(self)
    connection._endpoint = endpoint
    endpoint._connection = connection
    
    return endpoint

  def _connect(self, connection):
    """Executed in the network thread when the _ConnectEvent created by the
    connect method is run."""
    # Create the endpoint for this connection.
    endpoint = self._make_connecting_endpoint(connection)

    address = connection.address()
    endpoint._connect(address)

  def _connect_waiting(self, event):
    """Executed in the network thread when the _ConnectWaitingEvent created by
    the connect_waiting method is run."""
    # Create the endpoint for this connection.
    connection = event._connection
    endpoint = self._make_connecting_endpoint(connection)

    # Queue for received messages until the callback to connect_waiting is used.
    self._pending_conn_deques[connection] = collections.deque()

    address = connection.address()
    endpoint._connect(address, event.finish)

  def _connected_waiting(self, connection, separate):
    """Executed in the network thread when the _ConnectedWaitingEvent created by
    the connect_waiting method is run."""
    d = self._pending_conn_deques.pop(connection)

    if separate:
      self._conn_queues_lock.acquire()
      try:
        conn_queue = self._conn_queues[connection]
      finally:
        self._conn_queues_lock.release()

      # Populate the connection's separated queue with the received messages.
      conn_queue.extend(d)
    else:
      # Not separated, so populate the main queue with the received messages.
      self._recv_queue.extend(d)

  def _disconnect(self, connection, now, finish_message, finish_queue):
    """Executed in the network thread when the _DisconnectEvent created by the
    disconnect method is run."""
    connection._endpoint._disconnect(now, finish_message, finish_queue)

  def _send_message(self, connection, data):
    """Executed in the network thread when the _MessageEvent created by the
    send method is run."""
    connection._endpoint._enqueue(data)

  def _read_callback_used(self, endpoint):
    """Executed in the network thread when the _ReadCallbackCalledEvent created
    by using the read callback is run."""
    endpoint._read_callback_used()

  def _write_callback_used(self, endpoint):
    """Executed in the network thread when the _WriteCallbackCalledEvent
    created by using the write callback is run."""
    endpoint._write_callback_used()

  def _finish_disconnect(self, endpoint, incomplete, unsent):
    # Delivery any incomplete or unsent messages to the application.
    connection = endpoint._connection
    event = connector.FinishDisconnectEvent(connection, incomplete, unsent)
    self._enqueue_event(connection, event)

    # Don't let the client retaining the Connection instance retain the Endpoint.
    del connection._endpoint
    del endpoint._connection

    # Remove all state for the disconnected endpoint.
    with self._conn_queues_lock:
      del self._conn_queues[connection]

    if self._shutting_down:
      self._connections_shutting_down -= 1
      if not self._connections_shutting_down:
        # This is the last connection to shut down; end the loop and notify the application.
        self._complete_shutdown(self._shutdown_event)

  def _complete_shutdown(self, event):
    self._io_loop.stop()
    event.finished()

  def _shutdown(self, event):
    """Executed in the network thread when the _ShutdownEvent created by the
    shutdown method is run."""
    self._shutting_down = True

    # The listener disconnects trivially because it has no read or write buffers.
    if self._listener:
      self._listener._shutdown()
 
    if self._conn_queues:
      # Disconnect from each endpoint, writing to the specified point.
      self._connections_shutting_down = len(self._conn_queues)
      self._shutdown_event = event
      # If an endpoint calls _finish_disconnect it is removed from conn_queues, so copy.
      endpoints = [connection._endpoint for connection in self._conn_queues]
      for endpoint in endpoints:
        endpoint._disconnect(event._now, event._finish_messages, event._finish_queues)
    else:
      # No endpoints to disconnect from, so done already.
      self._complete_shutdown(event)

