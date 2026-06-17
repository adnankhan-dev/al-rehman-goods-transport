from ..extensions import db
from ..models import AppSetting


class SettingsRepository:
    def __init__(self, session=None):
        self.session = session or db.session

    def get_by_key(self, key):
        return self.session.query(AppSetting).filter(AppSetting.key == key).first()

    def set_value(self, key, value):
        setting = self.get_by_key(key)
        if setting is None:
            setting = AppSetting(key=key)
            self.session.add(setting)
        setting.value = value
        self.session.flush()
        return setting
