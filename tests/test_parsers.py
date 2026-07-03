"""Tests for multi-language AST parsers."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.parsers import parse_java, parse_python, parse_go, parse_javascript, parse_typescript, parse_regex


# ── Java ──

JAVA = '''\
package com.example;
import java.util.List;
@RestController
@RequestMapping("/api/v1/users")
public class UserController {
    private final UserService userService;
    public UserController(UserService svc) { this.userService = svc; }
    @GetMapping
    public List<UserDto> getUsers() { return userService.findAll(); }
    @PostMapping
    public UserDto createUser(@RequestBody UserDto dto) { return userService.create(dto); }
}
'''

def test_java_class():
    classes = parse_java(JAVA, "com.example")
    assert len(classes) == 1
    assert classes[0].name == "UserController"
    assert classes[0].kind == "class"
    assert classes[0].package == "com.example"

def test_java_methods():
    classes = parse_java(JAVA)
    names = [f.name for f in classes[0].functions]
    assert "getUsers" in names
    assert "createUser" in names

def test_java_annotations():
    classes = parse_java(JAVA)
    assert "@RestController" in classes[0].decorators

def test_java_imports():
    classes = parse_java(JAVA)
    assert len(classes[0].imports) >= 1

def test_java_interface():
    code = 'package com.example;\npublic interface ActorService {\n    List<ActorDto> findAll();\n}'
    classes = parse_java(code, "com.example")
    assert classes[0].kind == "interface"
    assert len(classes[0].functions) == 1


# ── Python ──

PYTHON = '''\
"""User service."""
from typing import List
class UserService:
    """Service for user operations."""
    def __init__(self, repo):
        self.repo = repo
    def get_all(self, page=0):
        """Get all users."""
        return self.repo.find_all(page)
    def _private(self):
        pass
def standalone(x, y):
    return x + y
'''

def test_python_class():
    classes = parse_python(PYTHON, "app.services")
    names = [c.name for c in classes]
    assert "UserService" in names

def test_python_methods():
    cls = [c for c in parse_python(PYTHON, "app") if c.name == "UserService"][0]
    names = [f.name for f in cls.functions]
    assert "get_all" in names

def test_python_private():
    cls = [c for c in parse_python(PYTHON, "app") if c.name == "UserService"][0]
    priv = [f for f in cls.functions if f.name == "_private"][0]
    assert not priv.is_public

def test_python_standalone():
    # Standalone functions wrapped in module class
    classes = parse_python("def hello():\n    pass\ndef add(a,b):\n    return a+b", "mod")
    assert any(f.name in ("hello", "add") for c in classes for f in c.functions)


# ── Go ──

GO = '''\
package user
import ("fmt"; "net/http")
type User struct {
    ID    int    `json:"id"`
    Name  string `json:"name"`
}
type UserService struct { repo UserRepository }
func (s *UserService) GetAll(page int) ([]User, error) { return nil, nil }
func (s *UserService) GetByID(id int) (*User, error) { return nil, nil }
func helper(name string) string { return fmt.Sprintf("Hi %s", name) }
'''

def test_go_structs():
    classes = parse_go(GO, "user")
    names = [c.name for c in classes]
    assert "User" in names
    assert "UserService" in names

def test_go_fields():
    classes = parse_go(GO, "user")
    user = [c for c in classes if c.name == "User"][0]
    field_names = [f["name"] for f in user.fields]
    assert "ID" in field_names
    assert "Name" in field_names

def test_go_methods():
    classes = parse_go(GO, "user")
    svc = [c for c in classes if c.name == "UserService"][0]
    names = [f.name for f in svc.functions]
    assert "GetAll" in names
    assert "GetByID" in names

def test_go_visibility():
    classes = parse_go(GO, "user")
    svc = [c for c in classes if c.name == "UserService"][0]
    fn = [f for f in svc.functions if f.name == "GetAll"][0]
    assert fn.is_public  # Uppercase = public in Go


# ── JavaScript ──

JS = '''\
class UserController {
    constructor(service) { this.service = service; }
    async getUsers(req, res) { return res.json([]); }
    createUser(req, res) { return res.json({}); }
}
export default UserController;
'''

def test_js_class():
    classes = parse_javascript(JS, "controllers")
    assert len(classes) >= 1
    assert classes[0].name == "UserController"

def test_js_methods():
    classes = parse_javascript(JS, "controllers")
    names = [f.name for f in classes[0].functions]
    assert "getUsers" in names or "constructor" in names


# ── Regex fallback ──

RUBY = '''\
class UserService
  def initialize(repo); @repo = repo; end
  def find_all; @repo.all; end
end
'''

def test_regex_fallback():
    classes = parse_regex(RUBY, "services")
    assert len(classes) >= 1
    assert classes[0].name == "UserService"


# ── Edge cases ──

def test_empty_code():
    assert parse_java("") == []
    assert parse_python("", "") == []
    assert parse_go("", "") == []
    assert parse_regex("", "") == []

def test_syntax_error():
    # tree-sitter handles bad code gracefully
    classes = parse_java("public class Foo { void bar( {")
    assert isinstance(classes, list)
