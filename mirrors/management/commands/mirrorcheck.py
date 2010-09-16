# -*- coding: utf-8 -*-
"""
mirrorcheck command

Poll every active mirror URL we have in the database, grab the 'lastsync' file,
and record details about how long it took and how up to date the mirror is. If
we encounter errors, record those as well.

Usage: ./manage.py mirrorcheck
"""

from django.core.management.base import NoArgsCommand
from django.db.models import Q

from datetime import datetime, timedelta
import logging
import re
import socket
import sys
import time
import thread
from threading import Thread
from Queue import Queue, Empty
import urllib2

from logging import ERROR, WARNING, INFO, DEBUG

from mirrors.models import Mirror, MirrorUrl, MirrorLog

logging.basicConfig(
    level=WARNING,
    format='%(asctime)s -> %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    stream=sys.stderr)
logger = logging.getLogger()

class Command(NoArgsCommand):
    help = "Runs a check on all known mirror URLs to determine their up-to-date status."

    def handle_noargs(self, **options):
        v = int(options.get('verbosity', 0))
        if v == 0:
            logger.level = ERROR
        elif v == 1:
            logger.level = INFO
        elif v == 2:
            logger.level = DEBUG

        import signal, traceback
        handler = lambda sig, stack: traceback.print_stack(stack)
        signal.signal(signal.SIGQUIT, handler)
        signal.signal(signal.SIGUSR1, handler)

        return check_current_mirrors()

def parse_rfc3339_datetime(time):
    # '2010-09-02 11:05:06+02:00'
    m = re.match('^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})([-+])(\d{2}):(\d{2})', time)
    if m:
        vals = m.groups()
        parsed = datetime(int(vals[0]), int(vals[1]), int(vals[2]),
                int(vals[3]), int(vals[4]), int(vals[5]))
        # now account for time zone offset
        sign = vals[6]
        offset = timedelta(hours=int(sign + vals[7]),
                minutes=int(sign + vals[8]))
        # subtract the offset, e.g. '-04:00' should be moved up 4 hours
        return parsed - offset
    return None

def check_mirror_url(mirror_url):
    url = mirror_url.url + 'lastsync'
    logger.info("checking URL %s" % url)
    log = MirrorLog(url=mirror_url, check_time=datetime.utcnow())
    try:
        start = time.time()
        result = urllib2.urlopen(url, timeout=10)
        data = result.read()
        result.close()
        end = time.time()
        # lastsync should be an epoch value, but some mirrors
        # are creating their own in RFC-3339 format:
        #     '2010-09-02 11:05:06+02:00'
        try:
            parsed_time = datetime.utcfromtimestamp(int(data))
        except ValueError:
            logger.debug("attempting to parse generated lastsync file"
                    " from mirror %s, value %s" % (url, data))
            parsed_time = parse_rfc3339_datetime(data)

        log.last_sync = parsed_time
        log.duration = end - start
        logger.debug("success: %s, %.2f" % (url, log.duration))
    except urllib2.HTTPError, e:
        log.is_success = False
        log.error =str(e)
        logger.debug("failed: %s, %s" % (url, log.error))
    except urllib2.URLError, e:
        log.is_success=False
        log.error = e.reason
        if isinstance(e.reason, socket.timeout):
            log.error = "Connection timed out."
        elif isinstance(e.reason, socket.error):
            log.error = e.reason.args[1]
        logger.debug("failed: %s, %s" % (url, log.error))

    log.save()
    return log

def mirror_url_worker(queue):
    while True:
        try:
            item = queue.get(block=False)
            check_mirror_url(item)
            queue.task_done()
        except Empty:
            return 0

class MirrorCheckPool(object):
    def __init__(self, work, num_threads=10):
        self.tasks = Queue()
        for i in work:
            self.tasks.put(i)
        self.threads = []
        for i in range(num_threads):
            thread = Thread(target=mirror_url_worker, args=(self.tasks,))
            thread.daemon = True
            self.threads.append(thread)

    def run_and_join(self):
        logger.debug("starting threads")
        for t in self.threads:
            t.start()
        logger.debug("joining on all threads")
        self.tasks.join()

def check_current_mirrors():
    urls = MirrorUrl.objects.filter(
            Q(protocol__protocol__iexact='HTTP') |
            Q(protocol__protocol__iexact='FTP'),
            mirror__active=True, mirror__public=True)

    pool = MirrorCheckPool(urls)
    pool.run_and_join()
    return 0

# For lack of a better place to put it, here is a query to get latest check
# result joined with mirror details:
# SELECT mu.*, m.*, ml.* FROM mirrors_mirrorurl mu JOIN mirrors_mirror m ON mu.mirror_id = m.id JOIN mirrors_mirrorlog ml ON mu.id = ml.url_id LEFT JOIN mirrors_mirrorlog ml2 ON ml.url_id = ml2.url_id AND ml.id < ml2.id WHERE ml2.id IS NULL AND m.active = 1 AND m.public = 1;

# vim: set ts=4 sw=4 et:
