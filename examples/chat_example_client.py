import argparse
import connector
import converters
import threading
import tornado_connector

def print_received_messages(c):
  class MessagePrinter(threading.Thread):
    def __init__(self):
      threading.Thread.__init__(self)
      self.daemon = True
    def run(self):
      # Block until a message to print is received.
      while True:
        event = c.recv()
        if event.type() == connector.Event.ENDPOINT_DISCONNECT:
          # TODO
          break
        elif event.type() == connector.Event.MESSAGE:
          print event.message()

  # Start the thread dedicated to printing messages.
  printer = MessagePrinter()
  printer.start()

def prompt_user_nickname(c, connection):
  nickname_taken = False
  while True:
    prompt = 'nickname taken, enter another: ' if nickname_taken else 'enter nickname: '
    nickname = raw_input(prompt).strip()
    nickname_taken = False
    if not nickname:
      continue
    c.send(connection, nickname)

    event = c.recv()
    if event.type() == connector.Event.MESSAGE:
      if event.message() == 'ok':
        break
      else:
        nickname_taken = True

def prompt_user_messages(c, connection):
  while True:
    s = raw_input('enter message (to quit, type "quit"): ').strip()
    if not s:
      continue
    if s == 'quit':
      c.shutdown(now=True)
      break

    c.send(connection, s)

def get_args():
  parser = argparse.ArgumentParser(description='Chat client')
  # TODO: validate flags
  parser.add_argument('--host', type=str, default='localhost',
                      help='hostname of server to connect to')
  parser.add_argument('--port', type=int, default=3453,
                      help='port of server to connect to')
  return parser.parse_args()

def get_connector():
  # Do not listen on any port.
  c = tornado_connector.TornadoConnector(converters.Primitives.STRING_CONVERTER)
  c.run(block=False)
  return c 

def main():
  args = get_args()
  c = get_connector()
  connection, success = c.connect_waiting(args.host, args.port)
  if success:
    prompt_user_nickname(c, connection)
    print_received_messages(c)
    prompt_user_messages(c, connection)
  else:
    print 'could not connect to host=%s, port=%d' % (args.host, args.port)

if __name__ == '__main__':
  main()

