#######################################################################
# This file is part of Lyntin.
# copyright (c) Free Software Foundation 2001, 2002
#
# Lyntin is distributed under the GNU General Public License license.  See the
# file LICENSE for distribution details.
# $Id: net.py,v 1.1 2003/04/29 21:42:35 willhelm Exp $
#######################################################################
"""
This holds the SocketCommunicator class which handles socket
connections with a mud and polling the connection for data.
"""
import socket, select, re
import event, ui.ui, lyntin, os, exported

### --------------------------------------------
### CONSTANTS
### --------------------------------------------

# reverse lookup allowing us to see what's going on more easily
# when we're debugging.
CODES = {255: "IAC",
         254: "DON'T",
         253: "DO",
         252: "WON'T",
         251: "WILL",
         250: "SB",
         249: "GA",
         240: "SE",
         239: "TELOPT_EOR",
         0:   "<IS>",
         1:   "[<ECHO> or <SEND>]",
         3:   "<SGA>",
         24:  "<TERMTYPE>",
         25:  "<EOR>", 
         31:  "<NegoWindoSize>",
         32:  "<TERMSPEED>",
         35:  "<XDISPLAY>",
         39:  "<ENV>"}

# telnet control codes
IAC  = chr(255)
DONT = chr(254)
DO   = chr(253)
WONT = chr(252)
WILL = chr(251)
SB   = chr(250)
GA   = chr(249)
NOP  = chr(241)
SE   = chr(240)
TELOPT_EOR = chr(239)
SEND = chr(1)
IS   = chr(0)

# some nice strings to help with the telnet control code
# negotiation
DD       = DO + DONT
WW       = WILL + WONT
DDWW     = DD + WW

# telnet option codes
ECHO     = chr(1)
SGA      = chr(3)
TERMTYPE = chr(24)
EOR      = chr(25)
NAWS     = chr(31)
ENV      = chr(39)


# the BELL character
BELL = chr(7)

class SocketCommunicator:
  """
  The SocketCommunicator handles all incoming and outgoing data from 
  and to the mud, telnet control codes, and some data transformations.
  """
  def __init__(self):
    self._sessionname = ''
    self._host = ''
    self._port = 0
    self._sock = None
    self._ansimode = 1
    self._nego_buffer = ''
    self._shutdownflag = 0
    self._session = None

    self._line_buffer = ''

    self._debug = 0

    # this is the prompt regex that we use to split the incoming text.
    self._prompt_regex = self._buildPromptRegex()

    # handle termtype issues
    if lyntin.options.has_key("term"):
      self._termtype = lyntin.options["term"][0]
    elif os.environ.has_key("TERM"):
      self._termtype = os.environ["TERM"]
    else:
      self._termtype = "lyntin"

    # we keep track of all the telnet stuff we're doing here
    # so we can look at it and dump it or whatever
    self._controllog = []

  def _buildPromptRegex(self, prompt=""):
    """
    Builds the prompt regex.  A prompt is IAC+GA or IAC+TELOPT_EOR or
    any string prompt.  Note that prompts eat up the characters.  So if
    the prompt is ">> " those characters will disappear from the stream.

    Note: the prompt is NOT escaped before it is added to the regexp.  It's
    up to you to re.escape the bits that need escaping.

    @param prompt: the text prompt to use (if any)
    @type  prompt: string

    @returns: the compiled regular expression for prompt detection
    @rtype: Regexp object
    """
    if prompt:
      r = "(" + IAC+GA + "|" + IAC+TELOPT_EOR + "|" + prompt + ")"
    else:
      r = "(" + IAC+GA + "|" + IAC+TELOPT_EOR + ")"
    return re.compile(r)

  def __repr__(self):
    return "connection %s %d" % (self._host, self._port)

  def logControl(self, str):
    self._controllog.append(str)

  def setSessionName(self, name):
    """
    Sets the session name.

    @param name: the new session name
    @type  name: string
    """
    self._sessionname = name

  def setSession(self, ses):
    """
    Sets the local session.  Each SocketCommunicator is matched
    up with a Session object.  This sets the Session object for
    this SocketCommunicator so we know who to pass information from
    the mud off to.

    @param ses: the session to set
    @type  ses: Session
    """
    # FIXME - maybe we should do dynamic lookup of the session every
    # time like we do with the ui?
    self._session = ses

  def shutdown(self):
    """
    Shuts down the thread polling the socket connection and the socket
    as well.
    """
    self._shutdownflag = 1

  def connect(self, host, port, sessionname):
    """
    Takes in a host and a port and connects the socket.

    @param host: the host to connect to
    @type  host: string

    @param port: the port to connect at
    @type  port: int

    @param sessionname: the name of the new session
    @type  sessionname: string
    """
    if not self._sock:
      sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
      sock.connect((host, port))
      sock.setblocking(1)

      self._host = host
      self._port = port
      self._sock = sock
      self._sessionname = sessionname
    else:
      raise Exception, "Connection already exists."

  def _pollForData(self):
    """
    Polls the socket for data.
    """
    readers, e, w = select.select([self._sock], [], [], .1)
    if readers:
      return readers[0].recv(1024)
    else:
      return None
  
  def run(self):
    """
    Polls a socket and returns any data sitting there.
    """
    try:
      data = None
      while not self._shutdownflag:
          newdata = self._pollForData()
          if newdata == '':
            if self._shutdownflag == 0 and self._session: 
              self._session.shutdown(())
            break

          if newdata:
            if data:
              data = data + newdata
            else:
              data = newdata
              
          if data:
            if data.endswith("\n") or not newdata:
              # actually handle whatever we've gotten because
              # we got a full line, or we polled for more and there was none.

              self.handleData(data)
              data = None

    except SystemExit:
      if self._session:
        self._session.shutdown(())

    except:
      exported.write_traceback("socket exception")
      if self._session:
        self._session.shutdown(())

    # if we hit this point, we want to shut down the socket
    try:    self._sock.shutdown(2)
    except: pass

    try:    self._sock.close()
    except: pass

    self._sock = None
    self._session = None

    # sometimes the mud will hose up with echo off--we want to kick it
    # on again.
    event.EchoEvent(1).enqueue()

    # output message so the user knows what happened.
    event.OutputEvent(ui.ui.Message("Lost connection to: %s\n" % self._host)).enqueue()

  def write(self, data, convert=1):
    """
    Writes data to the mud.

    @param data: the data to write to the socket
    @type  data: string

    @param convert: whether (1) or not (0) we should convert eol stuff to 
        CRLF and IAC to IAC IAC.
    @type  convert: boolean

    @raises Exception: if we have problems sending the data over the
        socket
    """
    if convert:
      data = data.replace("\n", "\r\n")

      if IAC in data:
        data = data.replace(IAC, IAC+IAC)

    if self._shutdownflag == 0:
      try:
        self._sock.send(data)

      except Exception, e:
        if self._shutdownflag == 0 and self._session:
          self._session.shutdown(())
          raise Exception, e

      return None
    return e

  def handleData(self, data):
    """
    Handles incoming data from the mud.  We wrap it in a MudEvent
    and toss it on the queue.

    @param data: the incoming data from the mud
    @type  data: string
    """
    global BELL

    data = self._nego_buffer + data

    # handle the bell
    count = data.count(BELL)
    for i in range(count):
      event.SpamEvent(exported.get_hook("bell_hook"), (self._session,)).enqueue()
    data = data.replace(BELL, "")

    # handle IAC GA, IAC TELOPT_EOR, and text prompts
    if lyntin.promptdetection == 1:
      splitdata = self._prompt_regex.split(data)
    else:
      splitdata = [data]

    # we split on prompts so that we serialize MudEvents with prompt_hook
    # calls.  this allows inline prompt detection in the stream.
    for d in splitdata:
      if lyntin.promptdetection and self._prompt_regex.match(d):
        event.SpamEvent(exported.get_hook("prompt_hook"), (self._session, d)).enqueue()
      else:
        
        # handle telnet option stuff
        if IAC in d:
          d = self.handleNego(d)

        # the rest of it is mud data
        event.MudEvent(self._session, d).enqueue()

    index = data.rfind("\n")
    if index == -1:
      self._line_buffer = data
    else:
      self._line_buffer = data[index:]

  def handleNego(self, data):
    """
    Removes telnet negotiation stuff from the stream and handles it.

    @param data: the incoming data from the mud that we need to parse
        for telnet control code stuff
    @type  data: string

    @return: the data without the telnet control codes
    @rtype:  string
    """
    marker = -1
    i = data.find(IAC)

    while (i != -1):
      if i + 1 >= len(data):
        marker = i
        break

      if data[i+1] == NOP:
        data = data[:i] + data[i+2:]
        self.logControl("receive: IAC NOP")

      elif data[i+1] == GA or data[i+1] == TELOPT_EOR:
        data = data[:i] + data[i+2:]

      elif data[i+1] == IAC:
        data = data[:i] + data[i+1:]
        i = i + 1
        self.logControl("receive: IAC IAC")

      else:
        if i + 2 >= len(data):
          marker = i
          break

        # handles DO/DONT/WILL/WONT stuff
        if data[i+1] in DDWW:
          if data[i+2] == ECHO:
            self.logControl("receive: IAC " + CODES[ord(data[i+1])]+" ECHO")
            if data[i+1] == WILL:
              event.EchoEvent(0).enqueue()
            elif data[i+1] == WONT:
              event.EchoEvent(1).enqueue()

          elif data[i+2] == TERMTYPE:
            self.logControl("receive: IAC " + CODES[ord(data[i+1])]+" TERMTYPE")
            if data[i+1] == DO:
              self.write(IAC + WILL + data[i+2], 0)
              self.logControl("send: IAC WILL TERMTYPE")
            else:
              self.write(IAC + WONT + data[i+2], 0)
              self.logControl("send: IAC WONT TERMTYPE")

          elif data[i+2] == EOR:
            self.logControl("receive: IAC " + CODES[ord(data[i+1])] + " EOR")
            if data[i+1] == WILL:
              self.write(IAC + DO + data[i+2], 0)
              self.logControl("send: IAC DO EOR")

          elif data[i+1] in DD:
            self.logControl("receive: IAC "+CODES[ord(data[i+1])]+" "+CODES[ord(data[i+2])])
            self.write(IAC + WONT + data[i+2], 0)
            self.logControl("send: IAC WONT " + CODES[ord(data[i+2])])

          data = data[:i] + data[i+3:]

        # handles SB...SE stuff
        elif data[i+1] == SB:

          end = data.find(SE, i)
          if end == -1:
            marker = i
            break

          if data[i+2] == TERMTYPE and data[i+3] == SEND:
            self.write(IAC + SB + TERMTYPE + IS + self._termtype + IAC + SE, 0)

          data = data[:i] + data[end+1:]

        # in case they passed us something weird we remove the IAC and 
        # move on
        else:
          data = data[:i] + data[i+1:]

      i = data.find(IAC, i)

    if marker != -1:
      self._nego_buffer = data[marker:]
      data = data[:marker]

    return data

# Local variables:
# mode:python
# py-indent-offset:2
# tab-width:2
# End:
