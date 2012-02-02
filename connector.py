class StreamSerializer:
  """Returns a stream of bytes for sending."""

  WAIT_FOR_CALLBACK = 0

  def get_bytes(self, callback):
    """Returns the next bytes for sending.

    This method must return one of the following values:
    * An empty string: the connector will no longer retrieve bytes from the
      stream and will return to using the serializer.
    * A non-empty string: the connector will append the returned bytes to the
      string of bytes to send over the connection.
    * WAIT_FOR_CALLBACK: the connector will not call this method again until the
      provided callback is used.
    """
    pass

class StreamDeserializer:
  """Accepts a stream of bytes for receiving."""

  WAIT_FOR_CALLBACK = 0
  WAIT_FOR_MORE = 1
  DONE = 2

  def put_bytes(self, data, callback):
    """Accepts the last bytes received.

    This method returns a pair of values, where the first is always a suffix of
    the given data which was not used. The second must be one of the following
    values:
    * WAIT_FOR_CALLBACK: The connector will not call this method again until the
      provided callback is used.
    * WAIT_FOR_MORE: The connector will not call again until it has more data to
      append to the suffix returned as the first value.
    * DONE: The connector will no longer write bytes to the stream and will
      return to using the deserializer.
    """
    pass

class Serializer:
  """Converts an object to a string of bytes for sending."""

  def get_bytes(self):
    """Returns the next bytes or the source of the next bytes for sending.

    This method must return one of the following values:
    * An empty string: The connector will no longer retrieve bytes from the
      serializer and will create a new serializer for the next message, if one
      is enqueued.
    * A non-empty string: The connector will append the returned bytes to the
      string of bytes to send over the connection.
    * A StreamSerializer instance: The connector will retrieve bytes for sending
      from the returned stream until the stream returns an empty string, upon
      which the connector will retrieve bytes from this serializer again.
    """
    pass

class Deserializer:
  """Converts a received string of bytes into an object."""

  WRITE_TO_STREAM = 0
  WAIT_FOR_MORE = 1
  DONE = 2

  def put_bytes(self, data):
    """Accepts the last bytes received.

    This method returns a tuple.

    The first element is always a suffix of the given data which was not used.

    The second element must be one of the following values:
    * WRITE_TO_STREAM: The connector will write received bytes to the stream
      returned as the third element in the tuple until the stream returns DONE,
      upon which the connector will write bytes to this deserializer again.
    * WAIT_FOR_MORE: The connector will not call again until it has more data to
      append to the suffix returned as the first value.
    * DONE: The connector will no longer write bytes to the deserializer and
      will create a new deserializer for the remainder or next received bytes.
      The deserialized message to pass to the application in a
      MessageReceivedEvent is returned as the third element in the tuple.

    The third element is only present if the second element is either
    WRITE_TO_STREAM or DONE. If WRITE_TO_STREAM, the third element is the stream
    to write following bytes to; if DONE, the third element is the deserialized
    message to pass to the application.
    """
    pass

class Converter:
  def get_serializer(self, data):
    """Returns a new Serializer instance that converts an object to a string of
    bytes for sending.
    """
    pass

  def get_deserializer(self):
    """Returns a new Deserializer instance that converts the received string of
    bytes to an object.
    """
    pass


class Connection:
  """A connection to an endpoint."""

  def __init__(self, address):
    self._address = address

  def address(self):
    """Returns the address of the endpoint as a (host, port) tuple."""
    return self._address


class Event:
  """Abstract base class for all events.
  """

  FINISH_CONNECT = 0
  FINISH_DISCONNECT = 1
  ENDPOINT_CONNECT = 2
  ENDPOINT_DISCONNECT = 3
  MESSAGE = 4
  REJECTED_MESSAGE = 5

  def __init__(self, connection):
    self._connection = connection

  def connection(self):
    """Returns the connection for which the event happened.
    """
    return self._connection

  def type(self):
    """Returns the type of the event.
    """
    return None

class FinishConnectEvent(Event):
  """Event for when the connector either succeeds or fails in creating a
  connection to an endpoint.
  """

  def __init__(self, connection, success, queued=None):
    Event.__init__(self, connection)
    self._success = success
    self._queued = queued

  def success(self):
    """Returns whether the connection was established successfully.
    """
    return self._success

  def queued(self):
    """If the connection did not connect successfully, a tuple of messages that
    were enqueued.
    """
    return self._queued

  def type(self):
    return Event.FINISH_CONNECT

class FinishDisconnectEvent(Event):
  """Event for when the connector finishes closing the connection to an
  endpoint.
  """

  def __init__(self, connection, incomplete, queued):
    Event.__init__(self, connection)
    self._incomplete = incomplete
    self._queued = queued or ()

  def incomplete(self):
    """Returns the message that was only partially sent when the connection was
    closed.

    If the last message was completely sent, this value is None. If a message
    is returned, the connection must have been disconnected with now=True.
    """
    return self._incomplete

  def queued(self):
    """Returns a tuple of messages enqueued when the connection was closed.

    If the last message enqueued was sent, either partially or incompletely,
    this tuple is empty. If this tuple is not empty, the connection must have
    been disconnected with either now=True or finish_message=True.
    """
    return self._queued

  def type(self):
    return Event.FINISH_DISCONNECT

class RemoteConnectEvent(Event):
  """Event for when an endpoint has created a connection to the connector.
  """

  def __init__(self, connection, callback):
    Event.__init__(self, connection)
    self._callback = callback

  def type(self):
    return Event.ENDPOINT_CONNECT

  def finish_accept(self, separate):
    self._callback(self._connection, separate)

class RemoteDisconnectEvent(Event):
  """Event for when an endpoint closes an established connection to it.
  """

  def __init__(self, connection, incomplete, queued):
    Event.__init__(self, connection)
    self._incomplete = incomplete
    self._queued = queued
  
  def incomplete(self):
    """Returns the message that was only partially sent when the connection was
    closed.

    If the last message was completely sent, this value is None.    
    """
    return self._incomplete

  def queued(self):
    """Returns a tuple of messages enqueued when the connection was closed.

    If the last message enqueued was sent, either partially or incompletely,
    this tuple is empty.
    """
    return self._queued

  def type(self):
    return Event.ENDPOINT_DISCONNECT

class MessageReceivedEvent(Event):
  """Event for when the connector receives a message from an endpoint.
  """

  def __init__(self, connection, message):
    Event.__init__(self, connection)
    self._message = message

  def message(self):
    """Returns a message received over the connection.
    """
    return self._message

  def type(self):
    return Event.MESSAGE

class RejectedMessageEvent(Event):
  """Event for when the connector could not send the message over the specified
  connection because the connection is either disconnecting or disconnected.
  """

  def __init__(self, connection, message):
    Event.__init__(self, connection)
    self._message = message

  def message(self):
    """Returns the message that could not be sent over the connection.
    """
    return self._message

  def type(self):
    return Event.REJECTED_MESSAGE


class Connector:
  """Manages Connection instances to endpoints."""

  def run(self):
    """Runs the connector, listening on the given port if specified.
    
    If no port was specified, then this connector can only communicate from
    connections established locally."""
    pass

  def connect(host, port, separate=False):
    """Starts connecting to the endpoint specified by the given host and port,
    returning the representative connection object.
    
    If separate is False, objects received on the connection are retrieved by
    calling recv. If separate is True, objects received on the connection are
    retrieved by calling recv_connection. This is useful for waiting for the
    first object that may identify the endpoint or establish any state. Once
    this object is received, calling merge_connection with the connection object
    will make all following messages retrievable by calling recv.
    """
    pass

  def connect_waiting(host, port, separate=False, callback=None, timeout=10.0):
    """Starts connecting to the endpoint specified by the given host and port,
    blocking until the connection succeeds or fails, or upon the given timeout
    expiring.
    
    If separate is False, objects received on the connection are retrieved by
    calling recv. If separate is True, objects received on the connection are
    retrieved by calling recv_connection. This is useful for waiting for the
    first object that may identify the endpoint or establish any state. Once
    this object is received, calling merge_connection with the connection object
    will make all following messages retrievable by calling recv.

    If callback is defined, it is called with the Connection instance as its
    first argument and a boolean value specifying whether connecting succeeded
    or failed as its second argument. Any received messages on the connection
    will not be available until this callback returns, which allows establishing
    any state to handle the connection beforehand.

    This function always returns the Connection instance and the boolean value
    that is passed as arguments to the callback method, if it was defined.
    """
    pass

  def disconnect(connection, now=False, finish_message=False, finish_queue=True):
    """Starts disconnecting from the given connection.
    
    If now is True, the connection is disconnected immediately. Any partially
    sent message and all queued messages are returned in a
    FinishDisconnectEvent.

    If finish_message is True, the connection is disconnected after any
    partially sent message is sent in is entirety. All queued messages are
    returned in a FinishDisconnectEvent.

    If finish_queue is True, the connection is disconnected after any partially
    sent message is sent in its entirety, and all queued messages are also sent.
    A FinishDisconnectEvent is returned.

    Regardless of which option is chosen, after this method is invoked, each
    message later enqueued by calling send will be returned in a
    FinishDisconnectEvent.
    """
    pass

  def send(self, connection, message):
    """Enqueues the given message for sending over the given connection.

    If the message cannot be delivered because the connection to the endpoint
    is either disconnecting or disconnected, the message will be returned in a
    RejectedMessageEvent.
    """
    pass

  def recv(self):
    """Blocks waiting for a new Event object.
    
    If this method returns None, then the thread that invoked shutdown has
    returned and so this connector is stopped and is not generating any more
    events.
    """
    pass

  def recv_connection(self, connection):
    """Blocks waiting for a new Event object associated with the given
    connection.

    This method blocks if the connection is not disconnected and was flagged as
    separate, meaning its events are not available through the recv method. If
    this method returns None, then one of the following must be true:
      * A thread that invoked shutdown has returned and so this connector is
        stopped and is not generating any more events.
      * This connection was not flagged as separate.
      * This connection was once flagged as separate, but it has been passed to
        merge_connection and its events are now available by calling recv.
    """
    pass

  def merge_connection(self, connection):
    """Makes all events for the given connection henceforth retrievable by
    calling recv instead of recv_connection.

    Any thread blocking on recv_connection with the given connection will return
    with a value of None.
    """
    pass

  def shutdown(self, now=False, finish_messages=False, finish_queues=True):
    """Shuts down the server, thereby refusing all new connections, starting to
    disconnect established ones, and cancelling all connecting ones.

    If now is True, each connection is disconnected immediately. For each
    connection, any partially sent message and all queued messages are returned
    in a FinishDisconnectEvent.

    If finish_messages is True, each connection is disconnected after any
    partially sent message is sent in is entirety. All queued messages are
    returned in a FinishDisconnectEvent.

    If finish_queues is True, each connection is disconnected after any
    partially sent message is sent in its entirety, and all queued messages are
    also sent. A FinishDisconnectEvent is returned.

    Regardless of which option is chosen, after this method is invoked, each
    message later enqueued by calling send will be returned in a
    FinishDisconnectEvent.
    """
    pass

