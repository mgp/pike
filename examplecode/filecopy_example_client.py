import argparse
import connector
import converters

def get_args():
  parser = argparse.ArgumentParser(description='Chat client')
  # TODO: validate flags
  parser.add_argument('--host', type=str, default='localhost',
                      help='hostname of server to connect to')
  parser.add_argument('--port', type=int, default=3453,
                      help='port of server to connect to')
  parser.add_argument('--filename', type=str, help='the filename to send')
  return parser.parse_args()

def get_connector():
  # Do not listen on any port.
  connector = connector.Connector(converter=converters.FileConverter)
  connector.run()
  return connector

def main():
  args = get_args()
  connector = get_connector()
  connection, success = connector.connect_waiting(args.host, args.port)
  if success:
    connector.send(connection, args.filename)
    connector.shutdown()
  else:
    print 'could not connect to host=%s, port=%d' % (args.host, args.port)

if __name__ == '__main__':
  main()

