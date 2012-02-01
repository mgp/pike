import argparse
import connector
import converters
import threading
import tornado_connector

class UserManager:
  def __init__(self, server_nickname):
    self._nicknames = set()
    self._connections = {}
    self._lock = threading.Lock()
    self._server_nickname = server_nickname

  def get_server_nickname(self):
    return self._server_nickname

  def add(self, nickname, connection):
    with self._lock:
      if nickname in self._nicknames:
        return False
      self._nicknames.add(nickname)
      self._connections[connection] = nickname
      return True

  def get_nickname(self, connection):
    with self._lock:
      return self._connections[connection]

  def get_all_connections(self):
    with self._lock:
      return self._connections.keys()

  def remove(self, connection):
    with self._lock:
      nickname = self._connections[connection]
      self._nicknames.remove(nickname)
      del self._connections[connection]

def start_relay(c, user_manager):
  class NicknameReceiver(threading.Thread):
    def __init__(self, connection):
      threading.Thread.__init__(self)
      self.daemon = True
      self._connection = connection

    def run(self):
      while True:
        event = c.recv_connection(self._connection)
        # TODO: handle disconnection, other events
        nickname = event.message()
        if nickname == user_manager.get_server_nickname():
          c.send(self._connection, 'nickname taken by server')
        elif user_manager.add(nickname, self._connection):
          c.send(self._connection, 'ok')
          c.merge_connection(self._connection)
          break
        else:
          c.send(self._connection, 'nickname taken by another user')

  class ChatRelay(threading.Thread):
    def __init__(self):
      threading.Thread.__init__(self)
      self.daemon = True

    def run(self):
      while True:
        event = c.recv()
        if event.type() == connector.Event.ENDPOINT_CONNECT:
          self._receive_nickname(event)
        elif event.type() == connector.Event.ENDPOINT_DISCONNECT:
          user_manager.remove(event.connection())
        elif event.type() == connector.Event.MESSAGE:
          self._receive_message(event)

    def _receive_nickname(self, event):
      event.finish_accept(separate=True)
      nickname_receiver = NicknameReceiver(event.connection())
      nickname_receiver.start()

    def _receive_message(self, event):
      nickname = user_manager.get_nickname(event.connection())
      message = '%s says: %s' % (nickname, event.message())
      print message
      for connection in user_manager.get_all_connections():
        if connection is not event.connection():
          c.send(connection, message)

  relay = ChatRelay()
  relay.start()

def prompt_until_quit(c, user_manager):
  while True:
    s = raw_input('enter message (to quit, type "quit"): ').strip()
    if not s:
      continue
    if s == 'quit':
      c.shutdown(finish_messages=True)
      break

    nickname = user_manager.get_server_nickname()
    message = '%s says: %s' % (nickname, s)
    for connection in user_manager.get_all_connections():
      c.send(connection, message)

def get_connector(port):
  # Listen on the specified port.
  c = tornado_connector.TornadoConnector(converters.Primitives.STRING_CONVERTER, port=port)
  c.run(block=False)
  return c

def get_args():
  parser = argparse.ArgumentParser(description='Chat server')
  # TODO: validate flags
  parser.add_argument('--port', type=int, default=3453,
                      help='port to listen on')
  parser.add_argument('--server_nickname', type=str, default='server',
                      help='nickname of the server')
  return parser.parse_args()

def main():
  args = get_args()
  c = get_connector(args.port)
  user_manager = UserManager(args.server_nickname)
  start_relay(c, user_manager)
  prompt_until_quit(c, user_manager)

if __name__ == '__main__':
  main()

