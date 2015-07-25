# Author: Nic Wolfe <nic@wolfeden.ca>
# URL: http://code.google.com/p/sickbeard/
#
# This file is part of SickRage.
#
# SickRage is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# SickRage is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with SickRage.  If not, see <http://www.gnu.org/licenses/>.

from __future__ import with_statement

import os.path
import re
import sqlite3
import time
import threading
import chardet
import sickbeard

from sickbeard import encodingKludge as ek
from sickbeard import logger
from sickbeard.exceptions import ex

db_cons = {}
db_locks = {}

def dbFilename(filename="sickbeard.db", suffix=None):
    """
    @param filename: The sqlite database filename to use. If not specified,
                     will be made to be sickbeard.db
    @param suffix: The suffix to append to the filename. A '.' will be added
                   automatically, i.e. suffix='v0' will make dbfile.db.v0
    @return: the correct location of the database file.
    """
    if suffix:
        filename = "%s.%s" % (filename, suffix)
    return ek.ek(os.path.join, sickbeard.DATA_DIR, filename)

class DBConnection(object):
    def __init__(self, filename="sickbeard.db", suffix=None, row_type=None):

        self.filename = filename
        self.suffix = suffix
        self.row_type = row_type

        try:
            if self.filename not in db_cons:
                db_locks[self.filename] = threading.Lock()

                self.connection = sqlite3.connect(dbFilename(self.filename, self.suffix), 20, check_same_thread=False)
                self.connection.text_factory = self._unicode_text_factory
                db_cons[self.filename] = self.connection
            else:
                self.connection = db_cons[self.filename]

            if self.row_type == "dict":
                self.connection.row_factory = self._dict_factory
            else:
                self.connection.row_factory = sqlite3.Row
        except Exception as e:
            logger.log(u"DB error: " + ex(e), logger.ERROR)
            raise

    def _execute(self, query, args):
        try:
            if not args:
                return self.connection.cursor().execute(query)
            return self.connection.cursor().execute(query, args)
        except Exception as e:
            raise

    def execute(self, query, args=None, fetchall=False, fetchone=False):
        try:
            if fetchall:
                return self._execute(query, args).fetchall()
            elif fetchone:
                return self._execute(query, args).fetchone()
            else:
                return self._execute(query, args)
        except Exception as e:
            raise

    def checkDBVersion(self):

        result = None

        try:
            if self.hasTable('db_version'):
                result = self.select("SELECT db_version FROM db_version")
        except:
            return 0

        if result:
            return int(result[0]["db_version"])
        else:
            return 0

    def mass_action(self, querylist=[], logTransaction=False, fetchall=False):
        # remove None types
        querylist = [i for i in querylist if i is not None and len(i)]

        sqlResult = []
        attempt = 0

        with db_locks[self.filename]:
            while attempt < 5:
                try:
                    for qu in querylist:
                        if len(qu) == 1:
                            if logTransaction:
                                logger.log(qu[0], logger.DEBUG)
                            sqlResult.append(self.execute(qu[0], fetchall=fetchall))
                        elif len(qu) > 1:
                            if logTransaction:
                                logger.log(qu[0] + " with args " + str(qu[1]), logger.DEBUG)
                            sqlResult.append(self.execute(qu[0], qu[1], fetchall=fetchall))
                    self.connection.commit()
                    logger.log(u"Transaction with " + str(len(querylist)) + u" queries executed", logger.DEBUG)

                    # finished
                    break
                except sqlite3.OperationalError, e:
                    sqlResult = []
                    if self.connection:
                        self.connection.rollback()
                    if "unable to open database file" in e.args[0] or "database is locked" in e.args[0]:
                        logger.log(u"DB error: " + ex(e), logger.WARNING)
                        attempt += 1
                        time.sleep(1)
                    else:
                        logger.log(u"DB error: " + ex(e), logger.ERROR)
                        raise
                except sqlite3.DatabaseError, e:
                    sqlResult = []
                    if self.connection:
                        self.connection.rollback()
                    logger.log(u"Fatal error executing query: " + ex(e), logger.ERROR)
                    raise

            #time.sleep(0.02)

            return sqlResult

    def action(self, query, args=None, fetchall=False, fetchone=False):
        if query == None:
            return

        sqlResult = None
        attempt = 0

        with db_locks[self.filename]:
            while attempt < 5:
                try:
                    if args == None:
                        logger.log(self.filename + ": " + query, logger.DB)
                    else:
                        logger.log(self.filename + ": " + query + " with args " + str(args), logger.DB)

                    sqlResult = self.execute(query, args, fetchall=fetchall, fetchone=fetchone)
                    self.connection.commit()

                    # get out of the connection attempt loop since we were successful
                    break
                except sqlite3.OperationalError, e:
                    if "unable to open database file" in e.args[0] or "database is locked" in e.args[0]:
                        logger.log(u"DB error: " + ex(e), logger.WARNING)
                        attempt += 1
                        time.sleep(1)
                    else:
                        logger.log(u"DB error: " + ex(e), logger.ERROR)
                        raise
                except sqlite3.DatabaseError, e:
                    logger.log(u"Fatal error executing query: " + ex(e), logger.ERROR)
                    raise

            #time.sleep(0.02)

            return sqlResult

    def select(self, query, args=None):

        sqlResults = self.action(query, args, fetchall=True)

        if sqlResults == None:
            return []

        return sqlResults

    def selectOne(self, query, args=None):

        sqlResults = self.action(query, args, fetchone=True)

        if sqlResults == None:
            return []

        return sqlResults

    def upsert(self, tableName, valueDict, keyDict):

        changesBefore = self.connection.total_changes

        genParams = lambda myDict: [x + " = ?" for x in myDict.keys()]

        query = "UPDATE " + tableName + " SET " + ", ".join(genParams(valueDict)) + " WHERE " + " AND ".join(
            genParams(keyDict))

        self.action(query, valueDict.values() + keyDict.values())

        if self.connection.total_changes == changesBefore:
            query = "INSERT INTO " + tableName + " (" + ", ".join(valueDict.keys() + keyDict.keys()) + ")" + \
                    " VALUES (" + ", ".join(["?"] * len(valueDict.keys() + keyDict.keys())) + ")"
            self.action(query, valueDict.values() + keyDict.values())

    def tableInfo(self, tableName):
        sqlResult = self.select("PRAGMA table_info(%s)" % tableName)
        columns = {}
        for column in sqlResult:
            columns[column['name']] = {'type': column['type']}
        return columns

    def _unicode_text_factory(self, x):
        try:
            # Already unicode, empty string, or ascii
            x = unicode(x)
        except Exception:
            try:
                # most common encoding from web
                x = unicode(x, 'utf-8')
            except Exception:
                try:
                    # most common from web if utf-8 fails
                    x = unicode(x, 'latin-1')
                except Exception:
                    try:
                        # try system encoding before trusting chardet
                        x = unicode(x, sickbeard.SYS_ENCODING)
                    except Exception:
                        try:
                            # Chardet can be wrong, so try it last before ignoring
                            x = unicode(x, chardet.detect(x).get('encoding'))
                        except Exception:
                            x = unicode(x, sickbeard.SYS_ENCODING, errors="ignore")
        return x

    def _dict_factory(self, cursor, row):
        d = {}
        for idx, col in enumerate(cursor.description):
            d[col[0]] = row[idx]
        return d

    def hasTable(self, tableName):
        return len(self.select("SELECT 1 FROM sqlite_master WHERE name = ?;", (tableName, ))) > 0

    def hasColumn(self, tableName, column):
        return column in self.tableInfo(tableName)

    def addColumn(self, table, column, type="NUMERIC", default=0):
        self.action("ALTER TABLE %s ADD %s %s" % (table, column, type))
        self.action("UPDATE %s SET %s = ?" % (table, column), (default,))

def sanityCheckDatabase(connection, sanity_check):
    sanity_check(connection).check()

class DBSanityCheck(object):
    def __init__(self, connection):
        self.connection = connection

    def check(self):
        pass


# ===============
# = Upgrade API =
# ===============

def upgradeDatabase(connection, schema):
    logger.log(u"Checking database structure..." + connection.filename, logger.DEBUG)
    _processUpgrade(connection, schema)


def prettyName(class_name):
    return ' '.join([x.group() for x in re.finditer("([A-Z])([a-z0-9]+)", class_name)])


def restoreDatabase(version):
    logger.log(u"Restoring database before trying upgrade again")
    if not sickbeard.helpers.restoreVersionedFile(dbFilename(suffix='v' + str(version)), version):
        logger.log_error_and_exit(u"Database restore failed, abort upgrading database")
        return False
    else:
        return True


def _processUpgrade(connection, upgradeClass):
    instance = upgradeClass(connection)
    logger.log(u"Checking " + prettyName(upgradeClass.__name__) + " database upgrade", logger.DEBUG)
    if not instance.test():
        logger.log(u"Database upgrade required: " + prettyName(upgradeClass.__name__), logger.DEBUG)
        try:
            instance.execute()
        except sqlite3.DatabaseError, e:
            # attemping to restore previous DB backup and perform upgrade
            try:
                instance.execute()
            except:
                restored = False
                result = connection.select("SELECT db_version FROM db_version")
                if result:
                    version = int(result[0]["db_version"])

                    # close db before attempting restore
                    connection.close()

                    if restoreDatabase(version):
                        # initialize the main SB database
                        upgradeDatabase(DBConnection(), sickbeard.mainDB.InitialSchema)
                        restored = True

                if not restored:
                    print "Error in " + str(upgradeClass.__name__) + ": " + ex(e)
                    raise
        logger.log(upgradeClass.__name__ + " upgrade completed", logger.DEBUG)
    else:
        logger.log(upgradeClass.__name__ + " upgrade not required", logger.DEBUG)

    for upgradeSubClass in upgradeClass.__subclasses__():
        _processUpgrade(connection, upgradeSubClass)


# Base migration class. All future DB changes should be subclassed from this class
class SchemaUpgrade(object):
    def __init__(self, connection):
        self.connection = connection

    def hasTable(self, tableName):
        return len(self.connection.select("SELECT 1 FROM sqlite_master WHERE name = ?;", (tableName, ))) > 0

    def hasColumn(self, tableName, column):
        return column in self.connection.tableInfo(tableName)

    def addColumn(self, table, column, type="NUMERIC", default=0):
        self.connection.action("ALTER TABLE %s ADD %s %s" % (table, column, type))
        self.connection.action("UPDATE %s SET %s = ?" % (table, column), (default,))

    def checkDBVersion(self):
        return self.connection.checkDBVersion()

    def incDBVersion(self):
        new_version = self.checkDBVersion() + 1
        self.connection.action("UPDATE db_version SET db_version = ?", [new_version])
        return new_version
