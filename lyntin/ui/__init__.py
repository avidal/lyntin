#######################################################################
# This file is part of Lyntin.
# copyright (c) Free Software Foundation 2001, 2002
#
# Lyntin is distributed under the GNU General Public License license.  See the
# file LICENSE for distribution details.
# $Id: __init__.py,v 1.1 2003/05/05 05:57:08 willhelm Exp $
#######################################################################
"""
This is the ui package.  Ui's to be used in Lyntin need to be dropped
in here.  ui's should extend the ui.BaseUI class and should also
implement the get_ui function that actually returns an instance of
the ui.

See the textui and tkui as examples.
"""
import os, sys

def get_ui(uiname):
  """
  Attempts to retrieve the ui by that name.

  @param uiname: the name of the ui passed in by the command line
  @type  uiname: string

  @return: a BaseUI subclass instance corresponding to the name
      of the ui the user wants to instantiate.  or None if the ui
      could not be found or instantiated.
  @rtype: BaseUI subclass
  """
  index = __file__.rfind(os.sep)
  if index == -1:
    path = "." + os.sep
  else:
    path = __file__[:index]

  if uiname + ".py" not in os.listdir(path):
    print "ui '%s' does not exist." % uiname
    return None

  try:
    path = "lyntin.ui." + uiname
    _module = __import__(path)
    _module = sys.modules[path]
    return _module.get_ui_instance()

  except Exception, e:
    print "get_ui: %s" % e

# Local variables:
# mode:python
# py-indent-offset:2
# tab-width:2
# End:
