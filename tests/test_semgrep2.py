import subprocess, os, pickle

def run_command(user_input):
    os.system("ls " + user_input)
    eval(user_input)
    subprocess.call(user_input, shell=True)
    pickle.loads(user_input)
