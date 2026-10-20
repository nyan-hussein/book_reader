import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()  # lets the installed app start its helper (background.py)
    from bookshelf.app import run
    run()
