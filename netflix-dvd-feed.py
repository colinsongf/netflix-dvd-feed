#!/usr/bin/python
import os
import types
import StringIO
import time
import codecs
import smtplib
import exceptions
import traceback
from optparse import OptionParser
import imaplib
import email
import re
import cgi
import ConfigParser

script_dir = ''
urlpat = re.compile('http://dvd.netflix.com/Movie/\d+')

# Read in custom configurations
config = ConfigParser.SafeConfigParser()
with open(os.path.basename(__file__).replace('.py','.cfg'), "r") as f:
    config.readfp(f)
G_NAME = config.get('global', 'name')
G_URL_BASE = config.get('global', 'url_base')
G_LOGFILE = config.get('global', 'logfile')
G_RSS_BASE = config.get('global', 'rss_base')
SMTP_USER = config.get('smtp', 'user')
SMTP_PASSWORD = config.get('smtp', 'password')
SMTP_FROM_ADDR = config.get('smtp', 'from')
IMAP_USER = config.get('imap', 'user')
IMAP_PASSWORD = config.get('imap', 'password')
IMAP_MAILBOX = config.get('imap', 'mailbox')

feed_header = """<?xml version="1.0" encoding="iso-8859-1"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
<channel>
<title>DVDs shipped for %s</title>
<link>http://dvd.netflix.com/Queue</link>
<atom:link href="http://%s/%s.xml" rel="self" type="application/rss+xml" />
<pubDate>%%s</pubDate>
<description>Feed automatically generated by %s</description>
<language>en-us</language>
""" % ( G_NAME, G_URL_BASE, G_RSS_BASE, G_URL_BASE )

feed_item = """<item>
<title>%s</title>
<pubDate>%s</pubDate>
<link>%s</link>
<guid isPermaLink="false">%s</guid>
<description>The disc &lt;a href="%s"&gt;%s&lt;/a&gt; was shipped on %s.</description>
</item>
"""

def send_email(subject, message, toaddrs, \
               fromaddr='"%s" <%s>' % (os.path.basename(__file__), SMTP_FROM_ADDR)):
    """ Sends Email """
    smtp = smtplib.SMTP('localhost')
    smtp.login(SMTP_USER, SMTP_PASSWORD)
    smtp.sendmail(fromaddr, 
                  toaddrs, 
                  "Content-Type: text/plain; charset=\"us-ascii\"\r\nFrom: %s\r\nTo: %s\r\nSubject: %s\r\n%s" % \
                  (fromaddr, ", ".join(toaddrs), subject, message))
    smtp.quit()

def write_feed(script_dir, feed_items):
    now = time.strftime("%a, %d %b %Y %H:%M:%S +0000", time.gmtime())
    index = 0
    do_move = False
    temp_fname = os.path.join(script_dir, G_RSS_BASE + '.temp.xml')
    dest_fname = os.path.join(script_dir, G_RSS_BASE + '.xml')
    with open(temp_fname, 'wb') as f:
        f.write(feed_header % (now,))
        for title, url, ship_date in reversed(feed_items):
            title = cgi.escape(title)
            guid = "%s+%s+%d" % (url, now, index)
            f.write(feed_item % (title,
                                  ship_date,
                                  url, 
                                  guid,
                                  url, title, ship_date[:-15]))
            index += 1
        f.write('</channel></rss>')
        do_move = True
    if do_move:
        os.rename(temp_fname, dest_fname)
        return "OK (Wrote %d new item%s.)" % (len(feed_items), len(feed_items) > 1 and "s" or "")
    return "Could not update the feed file."

def get_url_from_message(subject, part):
    txt = str(part)
    matches = urlpat.search(txt)
    if matches == None:
        return "NO", "The body no longer has the same type of URL."
    return "OK", matches.group(0)

def main(debug):
    global script_dir
    script_dir = os.path.abspath(os.path.dirname(sys.argv[0]))
    server = imaplib.IMAP4(IMAP_MAILBOX)
    server.login(IMAP_USER, IMAP_PASSWORD)
    server.select()
    status, data = server.search(None, 'ALL')
    if status != 'OK':
        raise Exception('Getting the list of all messages resulted in status %s' % (status))

    messages_to_delete = []
    feed_items = []
    for num in data[0].split():
        status, data = server.fetch(num, '(RFC822)')
        if status != 'OK':
            raise Exception('Fetching message %s resulted in status %s' % (num, status))
        msg = email.message_from_string(data[0][1])
        subject = msg['Subject']

        if subject.startswith('Fwd: '):
            subject = subject[5:]

        if subject.startswith("We've received: "):
            # We will bypass, but delete this message as recognized
            # print "Bypassing message", num, subject
            messages_to_delete.append(num)
            continue

        # Strip off the "For Xxx:"
        if not subject.startswith("For ") or subject.find(':') == -1:
            print 'Subject "%s" was unexpected, but the script is continuing. Please take a look at the mailbox.' % (subject)
            continue

        subject = subject.split(':', 1)[1].strip()

        got_url = False
        url = ""
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == "text/plain" or content_type == "text/html":
                # Now find the URL near the "Shipped" line.
                status, url = get_url_from_message(subject, part)
                if status == 'OK':
                    got_url = True
                    break

        if not got_url:
            raise Exception(url)

        messages_to_delete.append(num)
        # Append the disc name and movie URL to a list of items
        feed_items.append((subject, url, msg['Date']))

    # Ensure the new feed is written
    update_status = "OK"
    if len(feed_items) > 0:
        update_status = write_feed(script_dir, feed_items)

    if update_status.startswith("OK"):
        # Now delete only the messages marked for deletion
        for num in messages_to_delete:
            server.store(num, '+FLAGS', '\\Deleted')
        server.expunge()

    server.close()
    server.logout()
    print update_status
    return update_status

if __name__=='__main__':
    import sys
    parser = OptionParser()
    parser.add_option("-d", "--debug", action="store_true", dest="debug")
    parser.set_defaults(debug = False)
    options, args = parser.parse_args()
    start_time = time.time()
    if options.debug:
        message = main(options.debug)
    else:
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.stdout = sys.stderr = StringIO.StringIO()
        try:
            main(options.debug)
        except Exception, e:
            exceptional_text = "An exception occurred: " + str(e.__class__) + " " + str( e )
            print exceptional_text
            traceback.print_exc(file = sys.stdout)
            try:
                send_email('Exception thrown in %s' % (os.path.basename(__file__),),
                           exceptional_text + "\n" + traceback.format_exc(),
                           ('david.blume@gmail.com',))
            except Exception, e:
                print "Could not send email to notify you of the exception. :("

        message = sys.stdout.getvalue()
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    # Finally, let's save this to a statistics page
    if os.path.exists(os.path.join(script_dir, G_LOGFILE)):
        with codecs.open(os.path.join(script_dir, G_LOGFILE), 'r', 'utf-8') as f:
            lines = f.readlines()
    else:
        lines = []
    lines = lines[:168] # Just keep some recent lines 
    status = u'\n                       '.join(message.decode('utf-8').splitlines())
    lines.insert(0, u"%s %3.0fs %s\n" % (time.strftime('%Y-%m-%d, %H:%M', time.localtime()), \
                                         time.time() - start_time, 
                                         status))
    with codecs.open(os.path.join(script_dir, G_LOGFILE), 'w', 'utf-8') as f:
        f.writelines(lines)

