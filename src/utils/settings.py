from PyQt6.QtCore import QSettings

APP_NAME = "ACCELA"
ORG_NAME = "kaisma0"


def get_settings():

    return QSettings(ORG_NAME, APP_NAME)
