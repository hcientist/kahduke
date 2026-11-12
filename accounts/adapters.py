from allauth.socialaccount.adapter import DefaultSocialAccountAdapter


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    def populate_user(self, request, sociallogin, data):
        """Also fill KahDuke's display_name from the provider's full name."""
        user = super().populate_user(request, sociallogin, data)
        extra = sociallogin.account.extra_data
        name = data.get("display_name") or extra.get("name") or ""
        if name and not user.display_name:
            user.display_name = name[:60]
        return user
