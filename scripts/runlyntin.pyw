#!/usr/bin/env python
#######################################################################
# This file is part of Lyntin.
# copyright (c) Free Software Foundation 2001, 2002
#
# Lyntin is distributed under the GNU General Public License license.  See the
# file LICENSE for distribution details.
# $Id: runlyntin.pyw,v 1.1 2003/10/03 02:17:48 willhelm Exp $
#######################################################################
if __name__ == '__main__':
  import sys
  import optparse
  import lyntin
  from lyntin import engine, \
                     constants

  # ------------------------------------------------- #
  #  set up program options/switches                  #
  # ------------------------------------------------- #

  usage='usage: %prog [options] args'
  parser = optparse.OptionParser(usage)
  parser.add_option('-v','--version',
                    action='store_true',
                    dest='version',
                    default=False,
                    help='Display version and exit')
  parser.add_option('-c','--configfile',
                    dest='configfile',
                    help='Configuration file')
  parser.add_option('-u','--user-interface',
                    dest='ui',
                    default='',
                    help='User interface to use')
  parser.add_option('-d','--debug',
                    action='store_true',
                    dest='debug',
                    default=False,
                    help='Enable debugging messages')

  (options, args) = parser.parse_args()

  if options.version:
    print constants.VERSION
    sys.exit(0)

  engine_options = {}

  # Pass commandline options to engine
  if options.ui:
    engine_options['ui'] = options.ui
  if options.configfile:
    engine_options['configfile'] = options.configfile
  if options.debug:
    engine_options['debug'] = options.debug

  lyntin.engine.main(**engine_options)

# Local variables:
# mode:python
# py-indent-offset:2
# tab-width:2
# End:
