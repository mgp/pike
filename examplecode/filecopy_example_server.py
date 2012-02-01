import argparse
import connector
import converters

def add_file_writers(connector, num_threads, directory):
  class FileWriter(threading.Thread):
    def __init__(self, connection):
      threading.Thread.__init__(self)
      self.daemon = True
      self._connection = connection

    def run(self):
      while True:
        event = connector.recv_connection(self._connection)
        

  class Listener(threading.Thread):
    def __init__(self):
      threading.Thread.__init__(self)
      self.daemon = True
    def run(self):
      while True:
        event = connector.recv()
        if event.type == connector.Event.ACCEPTED:
          event.ack_accept(separate=False)
          file_writer = FileWriter(event.connection)
          file_writer.start()

def get_connector(port, num_threads, directory):
  # Listen on the specified port.
  connector = connector.Connector(converter=converters.StringConverter, port=port)
  add_file_writers(connector, num_threads, directory)
  connector.run()
  return connector

def prompt_until_quit(connector):
  while True:
    s = raw_input('to quit, type "quit": ').strip()
    if s == 'quit':
      connector.shutdown()
      break

def get_args():
  parser = argparse.ArgumentParser(description='Value doubler server')
  # TODO: validate flags
  parser.add_argument('--port', type=int, default=3453,
                      help='port of server to connect to')
  parser.add_argument('--num_threads', type=int, default=10,
                       help='number of threads to perform writing')
  parser.add_argument('--directory', type=string, default='.',
                      help='directory to save files to')
  return parser.parse_args()

def main():
  args = get_args()
  connector = get_connector(args.port, args.num_threads, args.directory)
  prompt_until_quit(connector)

