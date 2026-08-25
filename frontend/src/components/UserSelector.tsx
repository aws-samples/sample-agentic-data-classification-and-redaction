interface User {
  user_id: string;
  display_name: string;
  role: string;
  max_security_level: string;
  mnpi_cleared_entities: string[];
  pii_access: boolean;
}

interface Props {
  users: User[];
  selectedUser: User | null;
  onSelectUser: (user: User) => void;
}

function UserSelector({ users, selectedUser, onSelectUser }: Props) {
  return (
    <div className="user-selector">
      <label htmlFor="user-select">Persona:</label>
      <select
        id="user-select"
        value={selectedUser?.user_id || ''}
        onChange={(e) => {
          const user = users.find((u) => u.user_id === e.target.value);
          if (user) onSelectUser(user);
        }}
        aria-label="Select user persona"
      >
        {users.map((user) => (
          <option key={user.user_id} value={user.user_id}>
            {user.display_name} [{user.max_security_level}]
          </option>
        ))}
      </select>
    </div>
  );
}

export default UserSelector;
