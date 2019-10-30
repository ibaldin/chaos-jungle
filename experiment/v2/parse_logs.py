#! /usr/bin/env python3
import os
import sys
import fnmatch
from enum import IntEnum, auto
import csv
import subprocess

matrix = [] # output of parsing 
flowIDTable = {} # dictionary: key = (run+':'+relative_path+':'+src_node+':'+dest_node), value = flowID
csvfilepath = ''
result_dir = ''

class Field(IntEnum):
    FILENAME = 0
    PATH = auto()
    SRCNODE = auto()
    DESTNODE = auto()
    TIME_START = auto()
    TIME_END = auto()
    FILESIZE = auto()
    THROUGHPUT = auto()
    RETRIES = auto()
    FAILURE = auto() # Integrity Failure, file differs in this case
    MISSING = auto() # file missing
    SRC_R = auto()
    DEST_S = auto()
    LABEL = auto()
    RUN = auto()
    #FLOWID = auto()
    #KEY = auto()
    # following not filled yet
    #ORIGIN = auto()
    #OSGSITE = auto()


def debug_print(str):
    #print(str)
    return


def add_new_row_in_matrix():
    new_row=[]
    for j in range(len(Field)):
        new_row.append(0)
    matrix.append(new_row) #add fully defined column into the row

    
def get_row_in_matrix(key):
    if key in flowIDTable:
        debug_print('found key: ' + key)
        flowID = flowIDTable[key]
        #if matrix[flowID][Field.FLOWID] != flowID:
        #    debug_print('ERROR - flowID not matched')
    else:
        flowID = len(flowIDTable)
        flowIDTable[key] = flowID
        add_new_row_in_matrix() 
        matrix[flowID][Field.RUN] = key.split(':')[0]
        #matrix[flowID][Field.FLOWID] = flowID
        #matrix[flowID][Field.KEY] = key
    return flowID


def parse_logs(single_run):
    global matrix, flowIDTable, csvfilepath, result_dir
    matrix = []
    flowIDTable = {}
    iris_dir = os.environ['IRIS_DIR']

    # parse wget files from dest nodes
    for matched_file in fnmatch.filter(os.listdir(result_dir), '*{}_wget*'.format(single_run)):
        filepath = os.path.join(result_dir, matched_file)
        if os.path.isfile(filepath):
            filenumber = 0

            dest_node = os.path.basename(filepath).split('_')[0]
            src_node = os.path.basename(filepath).split('_')[3]
            src_node = src_node[:len(src_node)-4]
            run2=os.path.basename(filepath).split('_')[1]
            with open(filepath, "r") as f:
                #in_block = False
                l=0
                retry_count = 0
                for line in f:
                    l += 1
                    
                    if line.startswith('--'):
                        if retry_count == 0:
                            timestamp_start = line[2:].split('--')[0]
                            #print('time start = ' + timestamp_start + " " +str(l))

                    elif line.find('Retrying') >= 0:
                        retry_count += 1
                        debug_print ('\n\nRetrying, count = ' + str(retry_count))

                    elif line.find('Length') >= 0:
                        filesize = line.split()[1]

                    elif line.find('saved') >= 0:
                        transferred_file = line.split(u'\u2018')[1].split(u'\u2019')[0]
                        filename = os.path.basename(transferred_file)
                        
                        if filename.startswith('index.html'):
                            debug_print('ignore ' + transferred_file)
                            retry_count = 0
                            continue # do not parse this file

                        filenumber += 1
                        #in_block = True
                        idx = transferred_file.find('run')
                        (run, relative_path) = transferred_file[idx:].split('/',1)
                        dir_path = os.path.dirname(relative_path)
                        (timestamp_end, _rest) = line.split('(',2)
                        (throughput, _rest) = _rest.split(')',2)

                        debug_print('\n' + run)
                        debug_print(dir_path)
                        debug_print(filename)
                        debug_print('time start = ' + timestamp_start + ', end = '+ timestamp_end)
                        debug_print(throughput)
                        debug_print(filesize)
                        debug_print(retry_count)

                        flowID = get_row_in_matrix(run+':'+relative_path+':'+src_node+':'+dest_node)
                        
                        matrix[flowID][Field.FILENAME] = filename
                        matrix[flowID][Field.PATH] = dir_path
                        matrix[flowID][Field.SRCNODE] = src_node
                        matrix[flowID][Field.DESTNODE] = dest_node
                        matrix[flowID][Field.TIME_START] = timestamp_start
                        matrix[flowID][Field.TIME_END] = timestamp_end.strip()
                        matrix[flowID][Field.FILESIZE] = filesize
                        matrix[flowID][Field.THROUGHPUT] = throughput
                        matrix[flowID][Field.RETRIES] = retry_count
                        #matrix[flowID][Field.FILE] = matched_file
                        retry_count = 0

            # following checks whether there are missing files which are not in wget result
            filelist_file = os.path.join(result_dir, 'allfiles')
            miss_count = 0
            with open(filelist_file, "r") as f2:
                for line in f2:
                    relative_path = line.split()[0]
                    str_f = os.path.basename(relative_path)
                    str_d = os.path.dirname(relative_path)
                    flowID = get_row_in_matrix(run2+':'+relative_path+':'+src_node+':'+dest_node)

                    if matrix[flowID][Field.TIME_START] == 0 :
                        matrix[flowID][Field.FILENAME] = str_f
                        matrix[flowID][Field.PATH] = str_d
                        matrix[flowID][Field.SRCNODE] = src_node
                        matrix[flowID][Field.DESTNODE] = dest_node
                        matrix[flowID][Field.MISSING] = 1
                        miss_count += 1
            print ('Parsed wget {}, total {} records, missing {} records'.format(matched_file, filenumber, miss_count))

    # parse diff files from dest nodes
    for matched_file in fnmatch.filter(os.listdir(result_dir), '*{}_diff*'.format(single_run)):
        filepath = os.path.join(result_dir, matched_file)
        if os.path.isfile(filepath):
            with open(filepath, "r") as f:
                dest_node = os.path.basename(filepath).split('_')[0]
                src_node = os.path.basename(filepath).split('_')[3]
                src_node = src_node[:len(src_node)-4]
                debug_print('parsing '+ filepath)
                debug_print('dest_node = '+ dest_node)
                diff_count = 0
                miss_count = 0
                for line in f:
                    idx = filepath.find('run')
                    run2 = filepath[idx:].split('_')[0]

                    if line.strip().startswith('Files'):
                        corrupted_filename = line.split()[3]
                        
                        idx = corrupted_filename.find(iris_dir)
                        #src_node = corrupted_filename[idx+len(iris_dir)+1:].split('/')[0]

                        idx = corrupted_filename.find('run')
                        (run, relative_path) = corrupted_filename[idx:].split('/',1)
                        debug_print(run)
                        debug_print(relative_path)

                        filename = os.path.basename(relative_path)
                        dir_path = os.path.dirname(relative_path)

                        flowID = get_row_in_matrix(run+':'+relative_path+':'+src_node+':'+dest_node)
                        matrix[flowID][Field.FILENAME] = filename
                        matrix[flowID][Field.PATH] = dir_path
                        matrix[flowID][Field.SRCNODE] = src_node
                        matrix[flowID][Field.DESTNODE] = dest_node
                        matrix[flowID][Field.FAILURE] = 1
                        #matrix[flowID][Field.FILE] = matched_file
                        diff_count += 1
                print ('Parsed diff file {}, {} diffs'.format(matched_file, diff_count))

    # parse cj_log files from source nodes
    for matched_file in fnmatch.filter(os.listdir(result_dir), '*{}_cj.log'.format(single_run)):
        filepath = os.path.join(result_dir, matched_file)
        if os.path.isfile(filepath):
            with open(filepath, "r") as f:
                src_node = os.path.basename(filepath).split('_')[0]
                corrupt_count = 0
                for line in f:
                    if line.find('CORRUPT record') > 0:
                        corrupt_count += 1
                        corrupted_filename = line.split("'")[1]
                        debug_print("corrupted_filename = "+corrupted_filename)
                        idx = corrupted_filename.find('run')
                        (run, relative_path) = corrupted_filename[idx:].split('/',1)
                        debug_print(run)
                        debug_print(relative_path)
                        for key in flowIDTable.keys():
                            if key.startswith(run+':'+relative_path):
                                flowID = flowIDTable[key]
                                matrix[flowID][Field.LABEL] = src_node
                print ('Parsed file {}, {} corruptions'.format(matched_file, corrupt_count))
                print('----------------------------')
                print('<RUN>\t<NODE_STORAGE_CORRUPT/COUNT>')
                print('{}\t{}\t{}'.format(run, src_node, corrupt_count))
                print('----------------------------')

    # mark link corruption for specific runs
    for matched_file in fnmatch.filter(os.listdir(result_dir), os.environ['RUN_LINKLABEL_FILE']):
        filepath = os.path.join(result_dir, matched_file)
        if os.path.isfile(filepath):
            with open(filepath, "r") as f:
                for line in f:
                    (run, label) = line.split()
                    if run == single_run:
                        print('----------------------------')
                        print('<RUN>\t<LINK_CORRUPTED>')
                        print('{}\t{}'.format(run, label))
                        print('----------------------------')
                        for key in flowIDTable.keys():
                            debug_print(key)
                            if key.startswith(run):
                                flowID = flowIDTable[key]
                                matrix[flowID][Field.LABEL] = label #label.split('_')[1]

    for matched_file in fnmatch.filter(os.listdir(result_dir), '*node_router*'):
        filepath = os.path.join(result_dir, matched_file)
        if os.path.isfile(filepath):
            with open(filepath, "r") as f:
                debug_print('<NODEs>\t<ROUTERs>')
                for line in f:
                    (node, router) = line.split()
                    debug_print('{}\t{}'.format(node, router))
                    for key in flowIDTable.keys():
                        if key.find(node) > 0:
                            flowID = flowIDTable[key]
                            if matrix[flowID][Field.SRCNODE] == node:
                                matrix[flowID][Field.SRC_R] = router
                            elif matrix[flowID][Field.DESTNODE] == node:
                                matrix[flowID][Field.DEST_S] = router
                            else:
                                print('ERROR flowID = {}, {}'.format(flowID, key))

    with open(csvfilepath, 'a', newline='') as csvfile:
        writer = csv.writer(csvfile, delimiter=',',
                                quotechar='|', quoting=csv.QUOTE_NONE)
        for i in range(len(matrix)):
            writer.writerow(matrix[i])
    

def main():
    global csvfilepath, result_dir
    result_dir = sys.argv[1]
    if result_dir[len(result_dir)-1] == '/':
        result_dir = result_dir[:len(result_dir)-1]
    output_filename = os.path.basename(result_dir) + '.csv'
    print( 'output csv: ' + output_filename)
    csvfilepath = os.path.join(result_dir, output_filename)
    headers = []
    for field in Field:
        headers.append(field.name)
    with open(csvfilepath, 'w', newline='') as csvfile:
        writer = csv.writer(csvfile, delimiter=',', quotechar='|', quoting=csv.QUOTE_NONE)    
        writer.writerow(headers)

    filepath = os.path.join(result_dir, os.environ['RUN_LINKLABEL_FILE'])
    if os.path.isfile(filepath):
        with open(filepath, "r") as f:               
            for line in f:
                (run, label) = line.split()
    run_number = int(run[3:])
    print('total runs = {}'.format(run_number))

    for x in range(run_number):
        single_run = 'run{}'.format(x+1)
        print('parsing ' + single_run + '...')
        parse_logs(single_run)

    print( 'output csv: ' + csvfilepath + '\n')

if __name__ == "__main__":
    main()
