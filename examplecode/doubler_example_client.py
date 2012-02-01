import argparse
import connector
import converters
import tornado_connector

def prompt_user(c, connection):
  while True:
    s = raw_input('enter number: ').strip()
    if s == 'quit':
      c.shutdown(now=True)
      break
    try:
      f = float(s)
    except ValueError:
      print 'not a number! (to quit, type "quit")'
      continue

    c.send(connection, f) 
    event = c.recv()
    if event.type() == connector.Event.MESSAGE:
      print 'doubled value is %r' % event.message()
    else:
      print 'connection to server lost, exiting...'
      c.shutdown(now=True)
      break

def get_args():
  parser = argparse.ArgumentParser(description='Value doubler client')
  # TODO: validate flags
  parser.add_argument('--host', type=str, default='localhost',
                      help='hostname of server to connect to')
  parser.add_argument('--port', type=int, default=3453,
                      help='port of server to connect to')
  return parser.parse_args()

def get_connector():
  # Do not listen on any port.
  c = tornado_connector.TornadoConnector(converters.Primitives.FLOAT_CONVERTER)
  c.run(block=False)
  return c

def main():
  args = get_args()
  c = get_connector()
  connection, success = c.connect_waiting(args.host, args.port)
  if success:
    prompt_user(c, connection)
  else:
    print 'could not connect, exiting...'

if __name__ == '__main__':
  main()

