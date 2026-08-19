from app.db.models import Profile
from app.schemas.profile import ProfileDetailRead
from app.schemas.profile_settings import ProfileSettingsRead


class ProfileService:
    @staticmethod
    def detail(profile: Profile) -> ProfileDetailRead:
        profile_data = {
            "id": profile.id,
            "alias": profile.alias,
            "age": profile.age,
            "avatar": profile.avatar,
            "active": profile.active,
            "created_at": profile.created_at,
            "updated_at": profile.updated_at,
        }
        settings = (
            ProfileSettingsRead.model_validate(profile.settings)
            if profile.settings is not None
            else ProfileSettingsRead(profile_id=profile.id)
        )
        return ProfileDetailRead(**profile_data, settings=settings)
