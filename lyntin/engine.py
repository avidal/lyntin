#######################################################################
# This file is part of Lyntin.
# copyright (c) Free Software Foundation 2001, 2002
#
# Lyntin is distributed under the GNU General Public License license.  See the
# file LICENSE for distribution details.
# $Id: engine.py,v 1.8 2003/08/21 02:55:47 willhelm Exp $
#######################################################################
"""
This holds the X{engine} which both contains most of the other objects
that do work in Lyntin as well as encapsulates the event queue, event
handling methods, and some of the other singleton managers such as
the HelpManager and the CommandManager.

Engine also holds hooks to the various event types.  Events will call
all appropriate hooks allowing you to add functionality via the modules
interface without changing the Lyntin internals.

The engine also holds a list of registered threads.  This helps in
diagnostics.  Use the methods in exported to handle spinning off
threads.

The Engine class is a singleton and the reference to it is stored in
"engine.myengine".  However, you should use the exported module
to access the engine using the "get_engine()" function.

@var  myengine: The engine singleton.  Don't reference this though--it's
    better to go through the exported module.
@type myengine: Engine
"""
import Queue, thread, sys, traceback, os.path
from threading import Thread

from lyntin import config, session, utils, event, exported, helpmanager, history, commandmanager, constants

# this is the singleton reference to the Engine instance.
myengine = None

class Engine:
  """
  This is the engine class.  There should be only one engine.
  """
  def __init__(self):
    """ Initializes the engine."""

    # this is the event queue that holds all the events in
    # the system.
    self._event_queue = Queue.Queue()

    # this is a lock for writing stuff to the ui--makes sure
    # we're not hosing things by having multiple things write
    # to the ui simultaneously....  ick.
    self._ui_lock = thread.allocate_lock()

    # this is the master shutdown flag for the event queue
    # handling.
    self._shutdownflag = 0

    # this is the error count
    self._errorcount = 0

    # listeners exist at an engine level.  if you sign up for
    # an input hook, you get the input hook for ALL sessions.
    # this might change at some point....  we'll see.
    self._listeners = {}

    self._managers = {}

    # the help manager manages all the help content in a hierarchical
    # structure.
    self._managers["help"] = helpmanager.HelpManager()

    # our history manager
    self._managers["history"] = history.HistoryManager()

    # our command manager
    self._managers["command"] = commandmanager.CommandManager()

    # there is only one ui in the system.
    self._ui = None

    # current tick count
    self._current_tick = 0

    # list of registered threads
    self._threads = []

    # counts the total number of events processed--for diagnostics
    self._num_events_processed = 0

    # holds all the sessions
    self._sessions = {}

    # the current session.  points to a Session object.
    self._current_session = None

    # map of hook name -> utils.PriorityQueue objects
    self._hooks = {}

    # we register ourselves with the shutdown hook
    self.hookRegister("shutdown_hook", self.shutdown)

    commonsession = session.Session(self)
    commonsession.setName("common")

    # this creates a "common" entry in all the managers that manage
    # session scoped data
    # for mem in self._managers.values():
    #   mem.addSession(commonsession)

    self._sessions["common"] = commonsession
    self._current_session = commonsession

    self.hookRegister("user_filter_hook", self._managers["command"].filter, 100)


  ### ------------------------------------------
  ### hook stuff
  ### ------------------------------------------
  def getHook(self, hookname, newhook=1):
    """
    Retrieves the hook in question.  If the hook doesn't 
    exist and newhook==1, then we'll create a new hook.
    Otherwise, we'll return None.

    @param hookname: the name of the hook to retrieve
    @type  hookname: string

    @returns: the hook by name
    @rtype: utils.PriorityQueue
    """
    if self._hooks.has_key(hookname):
      return self._hooks[hookname]

    if newhook==1:
      self._hooks[hookname] = utils.PriorityQueue()
      return self._hooks[hookname]

    return None

  def hookRegister(self, hookname, func, place=constants.LAST):
    """
    Registers a function with a hook.

    @param hookname: the name of the hook
    @type  hookname: string

    @param func: the function to register with the hook
    @type  func: function

    @param place: the function will get this place in the call
        order.  functions with the same place specified will get
        arbitrary ordering.  defaults to constants.LAST.
    @type  place: int
    """
    hook = self.getHook(hookname)
    if place == None:
      hook.add(func)
    else:
      hook.add(func, place)
 
  ### ------------------------------------------
  ### thread stuff
  ### ------------------------------------------

  def startthread(self, name, func):
    """
    Starts a thread through the Thread Manager.

    @param name: the name of the thread to start
    @type  name: string

    @param func: the function to run in the thread
    @type  func: function
    """
    # clean up the list of threads that we maintain first
    self._threadCleanup()

    # create and initialize the new thread and stick it in our list
    t = Thread(None, func)
    t.setDaemon(1)
    t.setName(name)
    t.start()
    self._threads.append(t)

  def checkthreads(self):
    """
    Calls the Thread Manager checkthreads method which goes
    through and checks the status of all the threads registered
    with the Thread Manager.

    @return: one string for each thread indicating its status
    @rtype: list of strings
    """
    data = []
    for mem in self._threads:
      data.append("   %s %d" % (mem.getName(), mem.isAlive()))

    return data

  def _threadCleanup(self):
    """
    Removes threads which have ended.
    """
    removeme = []
    for i in range(len(self._threads)):
      if self._threads[i].isAlive() == 0:
        removeme.append(self._threads[i])

    for mem in removeme:
      self._threads.remove(mem)


  ### ------------------------------------------
  ### timer thread
  ### ------------------------------------------

  def runtimer(self):
    """
    This timer thread sleeps for a second, then calls everything
    in the queue with the current tick.

    Note: This will almost always be slightly behind and will
    get worse as there are more things that get executed each
    tick.
    """
    import time, event

    self._current_tick = 0
    while not self._shutdownflag:
      try:
        time.sleep(1)
        event.SpamEvent(hookname="timer_hook", 
                        argmap={"tick": self._current_tick}
                       ).enqueue()
        self._current_tick += 1
      except KeyboardInterrupt:
        return
      except SystemExit:
        return
      except:
        exported.write_traceback("ticker: ticker hiccupped.")


  ### ------------------------------------------
  ### input/output stuff
  ### ------------------------------------------

  def handleUserData(self, input, internal=0, session=None ):
    """
    This handles input lines from the user in a session-less context.
    The engine.handleUserData deals with global stuff and then
    passes the modified input to the session for session-oriented
    handling.  The session can call this method again with
    expanded input--this method is considered recursive.

    internal tells whether to spam the input hook and
    things of that nature.

    @param input: the data from the user
    @type  input: string

    @param internal: whether this should be executed internally or not.
        0 if we should spam the input hook and record
        the input to the historymanager; 1 if we shouldn't
    @type  internal: boolean

    @param session: the session scoping to execute this user input in
    @type  session: session.Session instance

    @return: the commands that were actually executed (may not be
        exactly what the user typed--this is for the history manager)
    @rtype: string
    """ 
    if config.debugmode == 1:
      exported.write_message("evaluating: %s" % input)

    inputlist = utils.split_commands(input)
    if session == None:
      session = self._current_session

    historyitems = []
    for mem in inputlist:
      mem = mem.strip()

      if len(mem) == 0:
        mem = config.commandchar + "cr"

      # if it's not internal we spam the hook with the raw input
      if internal == 0:
        exported.hook_spam("from_user_hook", {"data": mem})

      if mem.startswith("!"):
        memhistory = self.getManager("history").getHistoryItem(mem)
        if memhistory != None:
          self.handleUserData(memhistory, 1, session)
          historyitems.append(memhistory)
          continue

      # if it starts with a # it's a loop, session or command
      if len(mem) > 0 and mem.startswith(config.commandchar):

        # pull off the first token without the commandchar
        ses = mem.split(" ", 1)[0][1:]

        # is it a loop (aka repeating command)?
        if ses.isdigit():
          num = int(ses)
          if mem.find(" ") != -1:
            command = mem.split(" ", 1)[1]
            command = utils.strip_braces(command)
            if num > 0:
              for i in range(num):
                loopcommand = self.handleUserData(command, 1, session)
              historyitems.append(config.commandchar + ses + " {" + loopcommand + "}")
          continue

        # is it a session?
        if self._sessions.has_key(ses):
          input = mem.split(" ", 1)
          if len(input) < 2:
            self._current_session = self._sessions[ses]
            exported.write_message("%s now current session." % ses)
          else:
            self.handleUserData(input[1], internal=1, session=self._sessions[ses])
          historyitems.append(mem)
          continue

        # is it "all" sessions?
        if ses == "all":
          newinput = mem.split(" ", 1)
          if len(newinput) > 1:
            newinput = newinput[1]
          else:
            newinput = config.commandchar + "cr"

          for sessionname in self._sessions.keys():
            if sessionname != "common":
              self._sessions[sessionname].handleUserData(newinput, internal)
          historyitems.append(mem)
          continue

      # if we get here then it is not a valid !-expression. and it's going 
      # to the default session
      historyitems.append(mem)

      # no command char, so we pass it on to the session.handleUserData
      # to do session oriented things
      session.handleUserData(mem, internal)

    # we don't record internal stuff or input that isn't supposed
    # to be echo'd
    executed = ";".join(historyitems)
    if internal == 0 and config.mudecho == 1:
      self.getManager("history").recordHistory(executed)

    return executed

  def handleMudData(self, session, text):
    """
    Handle input coming from the mud.  We toss this to the 
    current session to deal with.

    @param session: the session this mud data applies to
    @type  session: session.Session instance

    @param text: the text coming from the mud
    @type  text: string
    """
    if session:
      session.handleMudData(text)
    else:
      exported.write_message("Unhandled data:\n%s" % text)


  ### ------------------------------------------
  ### session stuff
  ### ------------------------------------------

  def createSession(self, name):
    """
    Creates a new session by copying the common session
    and registers the new session with the engine.

    @param name: the name of the session
    @type  name: string

    @return: the new session
    @rtype: session.Session instance
    """
    ses = session.Session(self)
    ses.setName(name)
    self.registerSession(ses, name)
    return ses

  def registerSession(self, session, name):
    """
    Registers a session with the engine.

    @param session: the session to register
    @type  session: session.Session instance

    @param name: the name of the session
    @type  name: string

    @raises ValueError: if the session has a non-unique name
    """
    if self._sessions.has_key(name):
      raise ValueError("Session of that name already exists.")

    commonsession = self.getSession("common")
    for mem in self._managers.values():
      mem.addSession(session, commonsession)

    self._sessions[name] = session

  def unregisterSession(self, ses):
    """
    Unregisters a session from the engine.

    @param ses: the session to unregister
    @type  ses: session.Session instance
    """
    if not self._sessions.has_key(ses.getName()):
      raise ValueError("No session of that name.")

    for mem in self._managers.values():
      try:
        mem.removeSession(ses)
      except Exception, e:
        exported.write_error("Exception with removing session %s." % e)

    del self._sessions[ses.getName()]

    if ses == self._current_session:
      self.changeSession()

  def currentSession(self):
    """
    Returns the current session.

    @return: the current session
    @rtype: session.Session instance
    """
    return self._current_session

  def getSessions(self):
    """
    Returns a list of session names.

    @return: all the session names
    @rtype: list of strings
    """
    return self._sessions.keys()

  def getSession(self, name):
    """
    Returns a named session.

    @param name: the name of the session to retrieve
    @type  name: string

    @return: the session of that name or None
    @rtype: session.Session or None
    """
    if self._sessions.has_key(name):
      return self._sessions[name]
    else:
      return None

  def changeSession(self, name=''):
    """
    Changes the current session to another named session.

    If they don't pass in a name, we get the next available
    non-common session if possible.

    @param name: the name of the session to switch to
    @type  name: string
    """
    if name == '':
      keys = self._sessions.keys()

      # it's a little bit of finagling here to make sure
      # that the common session is the last one we would
      # switch to
      keys.remove("common")
      if len(keys) == 0:
        self._current_session = self._sessions["common"]
      else:
        self._current_session = self._sessions[keys[0]]

    # if they pass in a name, we switch to that session.
    elif self._sessions.has_key(name):
      self._current_session = self._sessions[name]

    else:
      exported.write_error("No session of that name.")
      return

    exported.write_message("Switching to session '%s'." % self._current_session.getName())

  def writeSession(self, message):
    """
    Writes a message to the network socket.  The message should 
    be a string.  Otherwise, it's unhealthy.

    @param message: the text to write to the mud
    @type  message: string
    """
    self._current_session.write(message)

  def closeSession(self, ses=None):
    """
    Closes down a session.

    @param ses: the name of the session to close
    @type  ses: string

    @return: 1 if successful; 0 if not
    @rtype: boolean
    """
    if ses == None:
      ses = self._current_session

    if ses.getName() == "common":
      exported.write_error("Can't close the common session.")
      return 0
         
    ses.shutdown((1,))
    self.unregisterSession(ses)
    exported.hook_unregister("shutdown_hook", ses.shutdown)
    return 1


  ### ------------------------------------------
  ### event-handling/engine stuff
  ### ------------------------------------------

  def _enqueue(self, event):
    """
    Adds an event to the queue.

    @param event: the new event to enqueue
    @type  event: event.Event
    """
    self._event_queue.put(event)

  def runengine(self):
    """
    This gets kicked off in a thread and just keep going through
    events until it detects a shutdown.
    """
    while not self._shutdownflag:
      try:
        # blocks on the event queue
        e = self._event_queue.get()
        e.execute()
      except KeyboardInterrupt:
        return
      except SystemExit:
        return
      except:
        self.tallyError()
        exported.write_traceback("engine: unhandled error in engine.")
      self._num_events_processed += 1
    sys.exit(0)
        
  def tallyError(self):
    """
    Adds one to the error count.  If we see more than 20 errors, we shutdown.
    """
    self._errorcount = self._errorcount + 1
    exported.write_error("WARNING: Unhandled error encountered (%d out of %d)." 
                         % (self._errorcount, 20))
    exported.hook_spam("error_occurred_hook", {"count": self._errorcount})
    if self._errorcount > 20:
      exported.hook_spam("too_many_errors_hook", {})
      exported.write_error("Error count exceeded--shutting down.")
      sys.exit("Error count exceeded--shutting down.")


  def shutdown(self, args):
    """ Sets the shutdown status for the engine."""
    self._shutdownflag = 1

  def getDiagnostics(self):
    """
    Returns some basic diagnostic information in the form of a string.
    This allows a user to monitor how Lyntin is doing in terms
    of events and other such erata.

    @return: the complete the complete diagnostic data for our little happy
        mud client
    @rtype: string
    """
    data = []
    data.append("   events processed: %d" % self._num_events_processed)
    data.append("   queue size: %d" % self._event_queue.qsize())
    data.append("   ui: %s" % repr(self._ui))
    data.append("   speedwalking: %d" % config.speedwalk)
    data.append("   ansicolor: %d" % config.ansicolor)
    data.append("   ticks: %d" % self._current_tick)
    data.append("   errors: %d" % self._errorcount)

    # print info from each session
    data.append("Sessions:")
    data.append("   total sessions: %d" % len(self._sessions))
    data.append("   current session: %s" % self._current_session.getName())

    for mem in self._sessions.values():
      # we do some fancy footwork here to make it print nicely
      info = "\n   ".join(self.getStatus(mem))
      data.append('   %s\n' % info)

    return "\n".join(data)


  ### ------------------------------------------
  ### user interface stuff
  ### ------------------------------------------

  def setUI(self, newui):
    """
    Sets the ui.

    @param newui: the new ui to set
    @type  newui: ui.base.BaseUI subclass
    """
    self._ui = newui

  def getUI(self):
    """
    Returns the ui.

    @return: the ui
    @rtype: ui.base.BaseUI subclass
    """
    return self._ui

  def writeUI(self, text):
    """
    Writes a message to the ui.

    This method uses a lock so that multiple threads can write
    to the ui without intersecting and crashing the python process.

    Theoretically you should use the exported module to write
    things to the ui--it calls this method.

    @param text: the message to write to the ui
    @type  text: string or ui.base.Message
    """
    self._ui_lock.acquire(1)
    try:
      exported.hook_spam("to_user_hook", {"message": text})
    finally:
      self._ui_lock.release()

  def writePrompt(self):
    """ Tells the ui to print a prompt."""
    if self._ui:
      self._ui.prompt()

  def flushUI(self):
    """ Tells the ui to flush its output."""
    self._ui.flush()

  
  ### ------------------------------------------------
  ### Manager functions
  ### ------------------------------------------------

  def addManager(self, name, manager):
    """
    Adds a manager to our list.

    @param name: the name of the manager to add
    @type  name: string

    @param manager: the manager instance to add
    @type  manager: manager.Manager subclass
    """
    self._managers[name] = manager

  def removeManager(self, name):
    """
    Removes a manager from our list.

    @param name: the name of the manager to remove
    @type  name: string

    @return: 0 if nothing happened, 1 if the manager was removed
    @rtype: boolean
    """
    if self._managers.has_key(name):
      del self._managers[name]
      return 1
    return 0

  def getManager(self, name):
    """
    Retrieves a manager by name.

    @param name: the name of the manager to retrieve
    @type  name: string

    @return: the manager instance or None
    @rtype: manager.Manager subclass or None
    """
    if self._managers.has_key(name):
      return self._managers[name]
    return None

  ### ------------------------------------------------
  ### Status stuff
  ### ------------------------------------------------
  def getStatus(self, ses):
    """
    Gets the status for a specific session.

    @param ses: the session to get status for
    @type  ses: session.Session

    @return: the status of the session
    @rtype: list of strings
    """
    # call session.getStatus() and get status from it too
    data = ses.getStatus()

    # loop through our managers and get status from them
    managerkeys = self._managers.keys()
    managerkeys.sort()

    for mem in managerkeys:
      temp = self.getManager(mem).getStatus(ses)
      if temp:
        data.append("   %s: %s" % (mem, temp))

    # return the list of elements
    return data


def shutdown():
  """
  This gets called by the Python interpreter atexit.  The reason
  we do shutdown stuff here is we're more likely to catch things
  here than we are to let everything cycle through the 
  ShutdownEvent.  This should probably get fixed up at some point
  in the future.

  Do not call this elsewhere.
  """
  import lyntin.hooks, lyntin.exported
  try:
    lyntin.exported.write_message("shutting down...  goodbye.")
  except:
    print "shutting down...  goodbye."
  lyntin.hooks.shutdown_hook.spamhook(())


def main(defaultui="text"):
  """
  This parses the command line arguments and makes sure they're all valid,
  instantiates a ui, does some setup, spins off an engine thread, and
  goes into the ui's mainloop.

  @param defaultui: the default ui to use.  for instance, the default
      ui for Win32 systems should probably be the tkui.
  @type  defaultui: string
  """
  global myengine
  startuperrors = []

  try:
    import sys, os, traceback
    from lyntin import config, event, utils, exported
    from lyntin.ui import base

    config.options["ui"] = defaultui

    # read through options and arguments
    optlist = utils.parse_args(sys.argv[1:])
    datadir = ""

    for mem in optlist:
      if mem[0] in ["--ui", "-u"]:
        config.options['ui'] = mem[1]

      elif mem[0] in ["--readfile", "--read", "-r"]:
        config.options['readfile'].append(mem[1])

      elif mem[0] in ["--moduledir", "-m"]:
        d = config.fixdir(mem[1])
        if d:
          config.options['moduledir'].append(d)
        else:
          startuperrors.append("Module dir '%s' does not exist." % mem[1])

      elif mem[0] in ["--datadir", "-d"]:
        d = config.fixdir(mem[1])
        if d:
          datadir = d
        else:
          startuperrors.append("Data dir '%s' does not exist." % mem[1])

      elif mem[0] == '--nosnoop':
        config.options['snoopdefault'] = 0

      elif mem[0] == '--help':
        print HELPTEXT
        sys.exit(0)

      elif mem[0] == '--version':
        print VERSION
        sys.exit(0)

      else:
        opt = mem[0]
        while len(opt) > 0 and opt[0] == "-":
          opt = opt[1:]

        if len(opt) > 0:
          if config.options.has_key(opt):
            config.options[opt].append(mem[1])
          else:
            config.options[opt] = [mem[1]]

    # if they haven't set the datadir via the command line, then
    # we go see if they have a HOME in their environment variables....
    if not datadir:
      if os.environ.has_key("HOME"):
        datadir = config.fixdir(os.environ["HOME"])

    config.options['datadir'] = datadir

    import atexit
    atexit.register(shutdown)

    # instantiate the engine
    myengine = Engine()

    # instantiate the ui
    uiinstance = None
    try:
      uiname = config.options['ui']
      modulename = uiname + "ui"
      uiinstance = base.get_ui(modulename)
      if not uiinstance:
        raise ValueError("No ui instance.")
    except Exception, e:
      print "Cannot start '%s': %s" % (uiname, e)
      traceback.print_exc()
      sys.exit(0)

    myengine.setUI(uiinstance)
    exported.write_message("UI started.")

    for mem in startuperrors:
      exported.write_error(mem)

    # do some more silly initialization stuff
    # adds the .lyntinrc file to the readfile list if it exists.
    if config.options['datadir']:
      lyntinrcfile = config.options['datadir'] + ".lyntinrc"
      if os.path.exists(lyntinrcfile):
        # we want the .lyntinrc file read in first, so then other
        # files can overwrite the contents therein
        config.options['readfile'].insert(0, lyntinrcfile)
  
    # import modules listed in modulesinit
    exported.write_message("Loading Lyntin modules.")
  
    try:
      import modules.__init__
      modules.__init__.load_modules()
    except:
      exported.write_traceback("Modules did not load correctly.")
      sys.exit(1)
  
    # spam the startup hook 
    exported.hook_spam("startup_hook", {})
  
    # handle command files
    for mem in config.options['readfile']:
      exported.write_message("Reading in file " + mem)
      # we have to escape windows os separators because \ has a specific
      # meaning in the argparser
      mem = mem.replace("\\", "\\\\")
      exported.lyntin_command("%sread %s" % (config.commandchar, mem), internal=1)
  
    # we're done initialization!
    exported.write_message(constants.STARTUPTEXT)
    myengine.writePrompt()

    # start the timer thread
    myengine.startthread("timer", myengine.runtimer)
    
    # we ask the ui if they want the main thread of execution and
    # handle accordingly
    if myengine._ui.wantMainThread() == 0:
      myengine.startthread("ui", myengine._ui.runui)
      myengine.runengine()
    else:
      myengine.startthread("engine", myengine.runengine)
      myengine._ui.runui()

  except SystemExit:
    # we do this because the engine is blocking on events....
    if myengine:
      event.ShutdownEvent().enqueue()
    
  except:
    import traceback
    traceback.print_exc()
    if myengine:
      try:
        event.ShutdownEvent().enqueue()
      except:
        pass
    sys.exit(1)

# Local variables:
# mode:python
# py-indent-offset:2
# tab-width:2
# End:
