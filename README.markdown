# Pike

Pike is a lightweight, thread-safe, message-oriented socket library in Python that is written atop the I/O loop of the [Tornado web server](http://www.tornadoweb.org/). It was written to make sending and receiving data over sockets extremely simple. If you care exclusively about high-performance, I suggest that you look into [Twisted](http://www.twistedmatrix.com/) or [ZeroMQ](http://www.zeromq.org/) instead.

## Design

The I/O loop of the Tornado web server is a single-threaded design based on the reactor pattern. 

Below, we use _endpoint_ as a synonym for the pairing of a port and an IP address or hostname to which the client can connect a socket.

Instances of the `Connection` class are opaque identifiers for the underlying sockets that are connected to endpoints.

## Connectors


The `Connector` interface is the hub through which connections are managed and data is both sent and received. The `TornadoConnector` implementation, found in `tornado_connector.py`, is built atop the I/O loop of the venerable Tornado web server. Alternate implementations may be included with Pike in the future.

### Starting and Stopping

The `run` method of a `Connector` instance must be called before it can be used. After this method returns, new connections to endpoints may be created and data may be sent and received over them using the methods in the following sections. If a port parameter was passed to the constructor of the connector, it begins listening for incoming connections on that port.

The `shutdown` method begins closing all connections to endpoints. After calling this method, the connector will reject any attempt to enqueue a message by returning it as part of a `RejectedMessageEvent`, described in **Events**. Additionally, the connector will refuse new incoming connections if it was listening on some port, and immediately terminate any connections that are still being established or for which the TCP handshake has not completed.

The `shutdown` method has the three optional parameters `now`, `finish_messages`, and `finish_queues`, exactly one of which must be passed in as `True`:

* If `now` is `True`, each connection is closed immediately. For each connection, any partially sent message and all enqueued messages are returned in a `FinishDisconnectEvent`, described in **Events**.

* If `finish_messages` is `True`, each connection is closed only after any partially sent message is sent in its entirety. All messages enqueued behind it are returned in a `FinishDisconnectEvent`.

* If `finish_queues` is `True`, each connection is closed only after any partially sent message is sent in its entirety and all enqueued messages are also sent. After this all happens, a `FinishDisconnectEvent` is returned.

### Connection Management

TODO

### Sending and Receiving Data

TODO

## Events

There are only six event types. Each is a subclass of `Event`, which has a `connection` accessor that returns the associated `Connection` instance. 

* `FinishConnectEvent` is returned when an attempt by the client to connect to an endpoint finishes, either by succeeding or failing. The `success` accessor returns whether connecting succeeded; if it did not succeed, the messages enqueued but not sent are returned by its `queued` accessor.

* `FinishDisconnectEvent` is returned when the client disconnects from an endpoint. When it disconnects, the client has the option of disconnecting immediately, disconnecting after completely sending whatever message is only partially sent, or disconnecting after sending all enqueued messages. If disconnecting immediately, the `incomplete` accessor returns the message that was not completely sent. If disconnecting immediately or after sending the message that is currently sending, the `queued` accessor returns any enqueued messages that were not sent.

* `RemoteConnectEvent` is returned when the client is listening for new connections and a remote endpoint is connecting. The `finish_accept` method completes the TCP handshake so that data can now be sent to and received from the endpoint. The `separate` parameter specifies whether its queue of incoming events for the associated connection should be managed separately; this is discussed in more detail below.

* `RemoteDisconnectEvent` is returned when a remote endpoint has disconnected from the client. If the remote endpoint disconnected unexpectedly, the client may have a message that is only partially sent to the endpoint, or still have additional messages enqueued. In this case, the `incomplete` accessor returns the message that was not completely sent, and the `queued` accessor returns the enqueued messages.

* `MessageReceivedEvent` is returned when a message has been received, or completely deserialized, from an endpoint. It can be retrieved using the `message` accessor.

* `RejectedMessageEvent` is returned after the client attempts to enqueue a message for a connection that is either in the process of disconnecting or is already disconnected. Note that this event will not be generated if a message is enqueued for a connection that is still connecting to a remote endpoint: If connecting succeeds, the enqueued message will be sent, but if connecting fails, a `FinishConnectEvent` will be created with the enqueued message returned by its `queued` accessor.

## Serialization and Deserialization

An implementation of the `Serializer` interface converts an enqueued object to a sequence of bytes that are sent. An implementation of the `Deserializer` interface converts a sequence of bytes that are received back into an object. Each operation is the inverse of the other.

### Converters

`Converter` is an interface for an object that serves as a factory for `Serializer` and `Deserializer` instances. By passing a `Converter` instance to the constructor of the `Connector`, you completely specify the wire protocol that the `Connector` uses.

The `Primitives` class in `converters.py` has the class variables `BOOL_CONVERTER`, `INT_CONVERTER`, `FLOAT_CONVERTER`, and `STRING_CONVERTER`, which are simple `Converter` implementations for the primitive types of booleans, integers, floating point values, and strings. If you are not sending one of these primitive types, you may need to define your own `Serializer` and `Deserializer` implementations as follows.

### Serializers

The `Serializer` interface defines a single method `get_bytes` that is called whenever the `Connector` instance is able to send more data to the endpoint. It returns one of the following values:

* If the empty string, then the object enqueued has been fully serialized and a new `Serializer` instance will be created for the next enqueued object.

* If a non-empty string, it is used as the next bytes to send over the connection.

* If the `WAIT_FOR_CALLBACK` sentinel value, the serializer may have more bytes for sending, but not immediately. The `Connector` instance will only call the `get_bytes` method again after the the serializer instance calls the given callback parameter. This is typically done if the serializer needs time to read the bytes from some slow resource.

### Deserializers

The `Deserializer` interface defines a single method `put_bytes` that is called whenever the `Connector` instance receives data from an endpoint. This method always returns a pair of values. The first value is always a suffix of the given data which was not used, and is potentially empty if the `Deserializer` instance consumes all the bytes. The second value is one of the following values:

* If the `DONE` sentinel value, then the object has been fully deserialized and a new `Deserializer` instance will be created for the unused suffix of bytes or whatever bytes are received next.

* If the `WAIT_FOR_MORE` sentinel value, the `Connector` instance will again call the `put_bytes` method, but only when additional bytes have been received from the endpoint. This is typically done if the bytes passed in do not even comprise a small, singular value of data that can be deserialized.

* If the `WAIT_FOR_CALLBACK` sentinel value, the `Connector` instance will only call the `put_bytes` method again after the deserializer instance calls the given callback parameter. This is typically done if the deserializer needs time to write the bytes recently consumed to some slow resource.

