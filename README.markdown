# Pike

Pike is a lightweight, thread-safe, message-oriented socket library in Python that is written atop the I/O loop of the [Tornado web server](http://www.tornadoweb.org/). It was written to make sending and receiving data over sockets extremely simple. If you care exclusively about high-performance, I suggest that you look into [Twisted](http://www.twistedmatrix.com/) or [ZeroMQ](http://www.zeromq.org/) instead.

## Design

The I/O loop of the Tornado web server is a single-threaded design based on the reactor pattern. 

Below, we use _endpoint_ as a synonym for the pairing of a port and an IP address or hostname to which the client can connect a socket.

Instances of the `Connection` class are opaque identifiers for the underlying sockets that are connected to endpoints.

## Events

There are only six event types. Each is a subclass of `Event`, which has a `connection` accessor that returns the associated `Connection` instance. 

* `FinishConnectEvent` is returned when an attempt by the client to connect to an endpoint finishes, either by succeeding or failing. The `success` accessor returns whether connecting succeeded; if it did not succeed, the messages enqueued but not sent are returned by its `queued` accessor.

* `FinishDisconnectEvent` is returned when the client disconnects from an endpoint. When it disconnects, the client has the option of disconnecting immediately, disconnecting after completely sending whatever message is only partially sent, or disconnecting after sending all enqueued messages. If disconnecting immediately, the `incomplete` accessor returns the message that was not completely sent. If disconnecting immediately or after sending the message that is currently sending, the `queued` accessor returns any enqueued messages that were not sent.

* `RemoteConnectEvent` is returned when the client is listening for new connections and a remote endpoint is connecting. The `finish_accept` method completes the TCP handshake so that data can now be sent to and received from the endpoint. The `separate` parameter specifies whether its queue of incoming events for the associated connection should be managed separately; this is discussed in more detail below.

* `RemoteDisconnectEvent` is returned when a remote endpoint has disconnected from the client. If the remote endpoint disconnected unexpectedly, the client may have a message that is only partially sent to the endpoint, or still have additional messages enqueued. In this case, the `incomplete` accessor returns the message that was not completely sent, and the `queued` accessor returns the enqueued messages.

* `MessageReceivedEvent` is returned when a message has been received, or completely deserialized, from an endpoint. It can be retrieved using the `message` accessor.

* `RejectedMessageEvent` is returned after the client attempts to enqueue a message for a connection that is either in the process of disconnecting or is already disconnected. Note that this event will not be generated if a message is enqueued for a connection that is still connecting to a remote endpoint: If connecting succeeds, the enqueued message will be sent, but if connecting fails, a `FinishConnectEvent` will be created with the enqueued message returned by its `queued` accessor.

## Serialization

TODO
