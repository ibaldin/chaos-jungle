#! /usr/bin/env python3
"""

The script will perform corruption for specific file or under specific folder

"""
import argparse
import os
import sys
import subprocess
import re
import random
import logging
import fnmatch
import shlex
import configparser
from cj_database import Database

config_file = 'cj_storage.cfg'

class Corruptor:

    def __init__(self, quiet, log_dir):

        random.seed()
        user_log = os.path.join(log_dir, 'cj.log')          # corruption history log for user
        debug_log = os.path.join(log_dir, 'cj_debug.log')   # debug log
        self._tmpfile = os.path.join(log_dir, 'cj.datablock')     # temporary file hold the corrupted block

        #setup logging
        formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
        handler1 = logging.FileHandler(debug_log)
        handler1.setFormatter(formatter)
        self.logger = logging.getLogger("CJlogger")
        self.logger.setLevel(logging.DEBUG)
        self.logger.addHandler(handler1)
        if not quiet:
            handler2 = logging.StreamHandler(sys.stdout)
            handler2.setLevel(logging.INFO)
            self.logger.addHandler(handler2)

        formatter3 = logging.Formatter('%(asctime)s %(message)s')
        handler3 = logging.FileHandler(user_log)
        handler3.setFormatter(formatter3)
        self.userlogger = logging.getLogger("CJuserlogger")
        self.userlogger.setLevel(logging.INFO)
        self.userlogger.addHandler(handler3)


    def get_file_info(self, filename):
        """ Return (file extent information, error msg)

        Get information of disk/extent of the file
        """

        global disk

        # df filename
        #
        # parse df output:
        # Filesystem              Type  Size  Used Avail Use% Mounted on
        # /dev/mapper/centos-root xfs    17G  1.8G   16G  11% /
        #
        command_line = 'df -Th {}'.format(filename)
        cmd_args = shlex.split(command_line)
        ret, output = self.call_subprocess(cmd_args)
        if ret != 0:
            return (None, "df cmd fail")

        lines = output.splitlines()
        if len(lines) >= 2 and (lines[0].strip().startswith('Filesystem')): # we expect 2 lines with first line start with 'Filesystem'
            (disk, fs_type, _) = lines[1].split(None, 2)
            self.logger.info('disk = {}, type = {}'.format(disk, fs_type))
        else:
            return (None, "unexpected df output")

        # filefrag -e
        #
        # Filesystem type is: 58465342
        # File size of /tmp/testfile.txt is 10485760 (2560 blocks of 4096 bytes)
        # ext:     logical_offset:        physical_offset: length:   expected: flags:
        #   0:        0..    2559:    1244591..   1247150:   2560:             eof
        # /tmp/testfile.txt: 1 extent found
        #
        command_line = '/usr/sbin/filefrag -b4096 -s -e {}'.format(filename)
        cmd_args = shlex.split(command_line)
        ret, output = self.call_subprocess(cmd_args)
        if ret != 0:
            return (None, "filefrag cmd fail")

        EXTRA_LINES = 4                         # as example above, we only need 3rd line
        lines = output.splitlines()
        extent_count = len(lines) - EXTRA_LINES
        if extent_count <= 0 :
            return (None, "unexpected filefrag output")

        array_extent_info = []
        for i in range(extent_count):
            (str_extent_number, _, _, str_begin, str_end, _) = re.split(r'\b\D+', lines[3+i], 5)
            extent_number = int(str_extent_number)
            begin = int(str_begin)
            end = int(str_end)
            self.logger.debug("ext={}, begin={}, end={}".format(extent_number, begin, end))
            if extent_number != i or begin == 0 or end == 0:
                return (None, "unexpected filefrag output")

            array_extent_info.insert(extent_number, [begin, end])
        return (array_extent_info, "")


    def corrupt_bit(self, filename, array_extent_info):
        """ This function issue dd linux command to corrupt the data in disk
            Reference: https://www.gnu.org/software/coreutils/manual/html_node/dd-invocation.html#dd-invocation
        """
        # pick a target block

        target_block = random.randint(array_extent_info[0][0], array_extent_info[0][1])
        self.logger.debug(array_extent_info)
        self.logger.debug('target_block is {}'.format(target_block))
        if target_block == 0:
            return -1   # something wrong, can't be 0

        # read the 1 block to tempfile using dd
        if self.dd_read_data(filename, disk, target_block) != 0:
            return -1

        # corrupt the bit in tmpfile
        # simple version: just corrupt the 7th bit (0x80) of 1st byte (0)
        nth_byte, nth_bit = 0,7
        result = self.perform_bit_inversion(nth_byte, nth_bit)
        target_value, modified_value = result[0], result[1]

        # write the 1-block length tmpfile back to disk
        if self.dd_write_data(filename, disk, target_block, 'CORRUPT_BIT') != 0:
            return -1
        self.logger.critical('Bit Inversion introduced to {}'.format(filename))
        self.logger.info('target_block = {}, nth_byte = {}, before/after: {}/{}'.format(target_block, nth_byte, hex(target_value), hex(modified_value)))

        # insert the record into database
        record = (filename, os.path.getmtime(filename), disk, target_block, nth_byte, target_value, modified_value)
        self.db.insert_record(record)
        self.userlogger.info('CORRUPT record: {}'.format(record))
            
        # drop the cache in file
        return self.dd_drop_cache(filename)


    def perform_bit_inversion(self, nth_byte, nth_bit):
        self.logger.debug('nth_byte = {}, nth_bit = {}'.format(nth_byte,7))
        with open(self._tmpfile, "rb+") as f:
            f.seek(nth_byte)
            target_value = f.read(1)
            if not target_value:
                return -1
            val = 0x01 << nth_bit
            modified_value = ord(target_value) ^ val
            f.seek(nth_byte)
            if sys.version_info[0] >= 3:
                f.write(modified_value.to_bytes(1, byteorder=sys.byteorder))
            else:
                f.write(chr(modified_value))
            self.logger.info('nth_byte = {} before/after: {}/{}'.format(nth_byte, hex(ord(target_value)), hex(modified_value)))
            return (ord(target_value), modified_value)


    def revert_all(self):
        records = self.db.get_all_records()
        for record in records:
            self.revert_data(record[1]) # record[1] is the filename


    def revert_data(self, filename):
        """ This function revert the corrupted data
            Reference: https://www.gnu.org/software/coreutils/manual/html_node/dd-invocation.html#dd-invocation
        """
        record = self.db.get_record_of_file(filename)
        if record is None:
            self.logger.info('\'{}\' record not found'.format(filename))
            return

        #record = (id, filename, os.path.getmtime(filename), disk, target_block, 1, orig_value, modified_value)
        self.logger.debug(record)
        record_mtime, record_disk, target_block, nth_byte, orig_value, modified_value = [record[i] for i in (2,3,4,5,6,7)] 
        self.logger.info('record: filename {}, record_disk {}, target_block {}, nth_byte {}, orig_value {}, modified_value {}'\
                        .format(filename, record_disk, target_block, nth_byte, hex(orig_value), hex(modified_value)))
        # check if the mtime is still not same as last time
        if record_mtime != os.path.getmtime(filename):
            self.logger.info('mtime not match! exiting...')
        
        self.logger.debug('target_block is {}'.format(target_block))
        if target_block == 0:
            return -1   # something wrong, can't be 0

        # read the 1 block to tempfile using dd
        if self.dd_read_data(filename, record_disk, target_block) != 0:
            return -1

        # revert to original value
        with open(self._tmpfile, "rb+") as f:
            # simple version: just corrupt the 1st bit of 1st byte
            f.seek(nth_byte)
            target_value = f.read(1)
            if not target_value:
                return -1

            self.logger.debug('modified_value {}/ value_read {}'.format(hex(modified_value), hex(ord(target_value))))
            if modified_value == ord(target_value):
                self.logger.debug('record value match')
            else:
                self.logger.info('modified_value {}/ value_read {} not match! exiting...'.format(hex(modified_value), hex(ord(target_value))))
                return -1

            f.seek(nth_byte)
            if sys.version_info[0] >= 3:
                f.write(orig_value.to_bytes(1, byteorder=sys.byteorder))
            else:
                f.write(chr(orig_value))
            self.logger.debug('revert to orig_value: {}'.format(hex(orig_value)))

        # write the 1-block length tmpfile back to record_disk
        if self.dd_write_data(filename, record_disk, target_block, 'REVERT') != 0:
            return -1
        self.logger.info('\'{}\' reverted'.format(filename))
        self.logger.info('target_block = {}, nth_byte = {}, before/after: {}/{}'.format(target_block, nth_byte, hex(ord(target_value)), hex(orig_value)))

        # delete the record into database
        self.db.delete_record_of_file(filename)
            
        # drop the cache in file
        return self.dd_drop_cache(filename)


    def call_subprocess(self, args):
        self.logger.debug(args)
        try:
            if sys.version_info[0] >= 3:
                output = subprocess.check_output(args,encoding='UTF-8', stderr=subprocess.STDOUT)
            else:
                output = subprocess.check_output(args, stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError:
            return (-1, None)
        else:
            self.logger.debug(output)
        return (0, output)


    def dd_read_data(self, filename, disk, target_block):
        skip_str = 'skip={}'.format(target_block)
        if_str = 'if={}'.format(disk)
        of_str = 'of={}'.format(self._tmpfile)
        cmd_args = ['dd', 'bs=4096', 'count=1', skip_str, if_str, of_str]
        ret, output = self.call_subprocess(cmd_args)
        if ret != 0:
            self.logger.error("dd_read_data fail")
        return ret


    def dd_write_data(self, filename, disk, target_block, op):
        self.userlogger.info('{} START filename = {}, target_block = {}'.format(op, filename, target_block))
        seek_str = 'seek={}'.format(target_block)
        if_str = 'if={}'.format(self._tmpfile)
        of_str = 'of={}'.format(disk)
        cmd_args = ['dd', 'bs=4096', 'count=1', if_str, of_str, seek_str, 'oflag=direct', 'conv=notrunc']
        ret, output  = self.call_subprocess(cmd_args)
        if ret == 0:
            self.userlogger.info('{} END success'.format(op))
        else:
            self.userlogger.info('{} END fail'.format(op))
            self.logger.error('dd_write_data() {} fail'.format(op))
        return ret


    def dd_drop_cache(self, filename):
        with open(filename, "rb+") as f:
            fd = f.fileno()
            os.fsync(fd)    # to make drop cache effective, first call fsync to force write to disk
        of_str = 'of={}'.format(filename)
        cmd_args = ['dd', of_str, 'oflag=nocache', 'conv=notrunc,fdatasync', 'count=0']
        ret, output = self.call_subprocess(cmd_args)
        return ret
        

    def has_been_corrupted(self, filename):
        """ read the record file and check if the file is already corrupted
        """
        record = self.db.get_record_of_file(filename)
        #self.logger.error(record)
        if record is not None:
            return True
        else:
            return False


    def corrupt_file(self, filename):
        """ Corrupt the file
        """
        if not os.path.isfile(filename) or os.path.getsize(filename) == 0:
            self.logger.warning('{}, file not existed or size = 0'.format(filename))
            return -1

        if self.has_been_corrupted(filename):
            self.logger.warning('file already corrupted')
            return -1

        array_extent_info, err = self.get_file_info(filename)
        if array_extent_info == None:
            self.logger.error('{} error = {}'.format(filename, err))
            return -1

        ret = self.corrupt_bit(filename, array_extent_info)
        if ret == -1:
            self.logger.warning('Not able to corrupt - data not changed')
            return -1
        return 0


    def corrupt_file_under_folder(self, path, pattern, recursive):
        """ pick one of the file under the specified directory and call corrupt_file()
        """
        self.logger.info('filename pattern = {}'.format(pattern))
        self.logger.warning('recursive = {}'.format(recursive))
        files = []

        if recursive:
            for root, dirs, filenames in os.walk(path):
                for matched_file in fnmatch.filter(filenames, pattern):
                    matched_filepath = os.path.join(root, matched_file)
                    if not self.has_been_corrupted(matched_filepath) and not os.path.getsize(matched_filepath) == 0:
                        files.append(matched_filepath)
        else:
            for matched_file in fnmatch.filter(os.listdir(path), pattern):
                matched_filepath = os.path.join(path, matched_file)
                if os.path.isfile(matched_filepath) \
                        and not self.has_been_corrupted(matched_filepath) \
                        and not os.path.getsize(matched_filepath) == 0:
                    files.append(matched_filepath)

        if not files:
            self.logger.info('no file to corrupt')
            return -1
        else:
            self.logger.info('files count = {}'.format(len(files)))
            victim_file = random.choice(files)
            self.logger.info('pick a victim: {}'.format(victim_file))
            return self.corrupt_file(os.path.abspath(victim_file))
        return 0


def run_corrupt(args):
    
    config = configparser.ConfigParser()
    dir_path = os.path.dirname(os.path.realpath(__file__))
    config_file_path = os.path.join(dir_path, config_file) 
    config.read(config_file_path)
    db_file = config['Paths']['database_file']
    log_dir = config['Paths']['log_dir']

    if not os.path.isdir(log_dir):
        print ('log_dir in config file doesnt exist')
        return

    cj = Corruptor(args.quiet, log_dir)
    cj.db = Database(cj.logger)

    if not db_file:
        cj.logger.error('DatabaseFile configuration error')
    else:
        if cj.db.connect(db_file) != 0:
            return
    cj.db.create_table()

    if args.revert:
        if args.target_file:
            for file in args.target_file:
                cj.revert_data(os.path.abspath(file))
        else:
            cj.revert_all()
        return

    if not args.probability or args.probability < 0 or args.probability > 1:
        args.probability = 1

    if not args.wait and not args.revert: # it is corrupt operation
        cj.logger.info('probability = {}'.format(args.probability))
        if random.random() >= args.probability:
            cj.logger.info('Nothing happened this time')
            return

    if not args.wait:
        if args.target_directory:
            if not os.path.isdir(os.path.abspath(args.target_directory)):
                print ('-d <directory> doesnt exist')
                return
            if not args.target_file:
                sys.exit('must provide file pattern by -f')
            else:
                cj.corrupt_file_under_folder(os.path.abspath(args.target_directory), args.target_file[0], args.recursive)
        elif args.target_file:
            for file in args.target_file:
                cj.corrupt_file(os.path.abspath(file))
        else:
            sys.exit('exit(): no file or directory given')

    else:
        if not args.target_directory or not args.target_file: 
            sys.exit('exit(): file and directory must be given')
        folder  = os.path.abspath(args.target_directory)
        pattern = args.target_file[0]
        if not os.path.isdir(folder):
            sys.exit('-d <directory> doesnt exist')

        cj.logger.info('waiting for file {} to arrive ...'.format(pattern))
        command_line = 'inotifywait  -e close_write --format \'%w%f\' -r -q {}'.format(folder)
        cmd_args = shlex.split(command_line)
        while 1:
            try:
                ret, output = cj.call_subprocess(cmd_args)
                if ret != 0:
                    return
                file = output.splitlines()[0]
                cj.logger.debug(file)
                if args.recursive:
                    if fnmatch.fnmatch(os.path.basename(file), pattern):
                        cj.corrupt_file(file)
                        break
                else:
                    if fnmatch.fnmatch(os.path.basename(file), pattern) and os.path.dirname(file) == os.path.abspath(args.target_directory) :
                        cj.corrupt_file(file)
                        break
            except KeyboardInterrupt:
                break


def main():
    parser = argparse.ArgumentParser(description='[WARNING!] The program corrupts file(s), please use it with CAUTION!')
    parser.add_argument('-f', dest="target_file", nargs='*',
                        help='the path of target file or the pattern of filename (pattern should be wrapped by "") to corrupt. e.g.: -f /tmp/abc.txt, -f "*.txt", -f "*"')
    parser.add_argument('-d', dest="target_directory",
                        help='the directory, under which the files will randomly selected to be corrupted')
    parser.add_argument('-r', '--recursive', action='store_true', default=False, help='match the files within the directory and its entire subtree (default: False)')
    parser.add_argument('-p', dest='probability', type=float, help='the probability of corruption (default: 1.0)')
    parser.add_argument('-q', '--quiet', action='store_true', help='Be quiet')
    parser.add_argument('--wait', action='store_true', help='wait and corrupt a single file [-f "pattern"] under folder [-d <directory>]')
    parser.add_argument('--revert', action='store_true', help='revert the specified corrupted file [-f <file>] or all files if -f is omitted')
    parser.add_argument('-db', dest="db_file", help='database file to replay')

    # following are dummy arguments from cj_service.py
    parser.add_argument('--start', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--stop', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('-F', dest="freq", help=argparse.SUPPRESS)
    parser.add_argument('--onetime', action='store_true', help=argparse.SUPPRESS)

    parser.set_defaults(func=run_corrupt)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
