import argparse
import connector
import converters
import threading
import tornado_connector

def add_doublers(c, num_threads):
  class Doubler(threading.Thread):
    def __init__(self):
      threading.Thread.__init__(self)
      self.daemon = True
    def run(self):
      while True:
        # Block until a value to double is received.
        event = c.recv()
        if event.type() == connector.Event.ENDPOINT_CONNECT:
          event.finish_accept(separate=False)
        elif event.type() == connector.Event.MESSAGE:
          try:
            # Send back the doubled value.
            doubled_value = 2 * event.message()
            c.send(event.connection(), doubled_value)
          except ValueError:
            error_msg = 'undefined for invalid value "%s"' % event.data
            c.send(event.connection(), error_msg)

  # Start the threads dedicated to doubling values.
  for i in xrange(num_threads):
    doubler = Doubler()
    doubler.start()

def get_connector(port, num_threads):
  # Listen on the specified port.
  c = tornado_connector.TornadoConnector(converters.Primitives.FLOAT_CONVERTER, port=port)
  add_doublers(c, num_threads)
  c.run(block=False)
  return c

def prompt_until_quit(c):
  while True:
    s = raw_input('to quit, type "quit": ').strip()
    if s == 'quit':
      c.shutdown()
      break

def get_args():
  parser = argparse.ArgumentParser(description='Value doubler server')
  # TODO: validate flags
  parser.add_argument('--port', type=int, default=3453,
                      help='port to listen on')
  parser.add_argument('--num_threads', type=int, default=10,
                       help='number of threads to perform doubling')
  return parser.parse_args()

def main():
  args = get_args()
  c = get_connector(args.port, args.num_threads)
  prompt_until_quit(c)

if __name__ == '__main__':
  main()

