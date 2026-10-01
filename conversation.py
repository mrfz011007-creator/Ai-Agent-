from google.genai import types


class ConversationMemory:

    def __init__(self):
        self.contents = []

    def add_user_message(self, text):
        self.contents.append(
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(
                        text=text
                    )
                ]
            )
        )

    def add_model_message(self, content):
        self.contents.append(
            content
        )

    def add_tool_response(self, parts):
        self.contents.append(
            types.Content(
                role="user",
                parts=parts
            )
        )

    def get_contents(self):
        return self.contents

    def clear(self):
        self.contents = []
