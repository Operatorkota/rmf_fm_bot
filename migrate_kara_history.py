import json

def migrate_kara_history():
    try:
        with open('kara_history.json', 'r', encoding='utf-8') as f:
            old_data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        print("Plik kara_history.json nie został znaleziony lub jest uszkodzony.")
        return

    new_data = {}
    for user_id, user_history in old_data.items():
        if "punishments" in user_history:
            # Already in new format
            new_data[user_id] = user_history
            continue

        new_user_history = {
            "punishments": [],
            "current_role_id": user_history.get("current_role_id")
        }

        for strefa, count in user_history.items():
            if strefa in ["zielona", "żółta", "czerwona"]:
                for _ in range(count):
                    new_user_history["punishments"].append({
                        "strefa": strefa,
                        "reason": "(brak danych - migracja)",
                        "moderator": "(brak danych - migracja)",
                        "date": "(brak danych - migracja)",
                        "mute_duration": None
                    })
        
        new_data[user_id] = new_user_history

    try:
        with open('kara_history_new.json', 'w', encoding='utf-8') as f:
            json.dump(new_data, f, indent=4, ensure_ascii=False)
        print("Migracja zakończona pomyślnie. Utworzono plik kara_history_new.json.")
        print("Zmień nazwę pliku kara_history_new.json na kara_history.json, aby zakończyć proces.")
    except Exception as e:
        print(f"Wystąpił błąd podczas zapisu nowego pliku: {e}")

if __name__ == '__main__':
    migrate_kara_history()
